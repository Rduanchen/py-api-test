"""Statistics collection, percentile aggregation, and report generation."""

import csv
import io
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.models import AggregatedMetricsTick, MetricsUpdatePayload, TestSummaryReport

logger = logging.getLogger("master.statistics")

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def calculate_percentiles(latencies: List[float], percentiles: List[float] = [50, 95, 99]) -> Dict[str, float]:
    if not latencies:
        return {f"p{int(p)}": 0.0 for p in percentiles}
    sorted_lat = sorted(latencies)
    n = len(sorted_lat)
    result = {}
    for p in percentiles:
        k = (p / 100.0) * (n - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            val = sorted_lat[int(k)]
        else:
            val = sorted_lat[int(f)] * (c - k) + sorted_lat[int(c)] * (k - f)
        result[f"p{int(p)}"] = round(val, 2)
    return result


class StatisticsAggregator:
    def __init__(self, test_id: str, target_url: str, mode: str, exam_id: str):
        self.test_id = test_id
        self.target_url = target_url
        self.mode = mode
        self.exam_id = exam_id
        self.start_time = time.time()
        self.end_time: Optional[float] = None

        # Per-worker cumulative metrics
        self.worker_metrics: Dict[str, MetricsUpdatePayload] = {}
        # Latencies collected during current tick
        self.tick_latencies: List[float] = []
        # All latencies for final report (downsampled if very large)
        self.all_latencies: List[float] = []

        self.time_series: List[Dict[str, Any]] = []
        self.peak_rps: float = 0.0

        # Cumulative totals
        self.last_total_requests = 0
        self.last_tick_time = self.start_time

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def record_worker_update(self, payload: MetricsUpdatePayload):
        self.worker_metrics[payload.worker_id] = payload
        if payload.latencies_ms:
            self.tick_latencies.extend(payload.latencies_ms)
            # Store for global percentile calculation
            if len(self.all_latencies) < 50000:
                self.all_latencies.extend(payload.latencies_ms)

    def tick(self) -> AggregatedMetricsTick:
        now = time.time()
        elapsed = now - self.start_time
        tick_interval = max(0.001, now - self.last_tick_time)

        total_reqs = sum(w.total_requests for w in self.worker_metrics.values())
        failed_reqs = sum(w.failed_requests for w in self.worker_metrics.values())
        active_vu = sum(w.active_vu for w in self.worker_metrics.values())
        socket_conns = sum(w.socket_connected for w in self.worker_metrics.values())
        active_workers = len(self.worker_metrics)

        # Delta RPS for this tick
        delta_reqs = max(0, total_reqs - self.last_total_requests)
        current_rps = round(delta_reqs / tick_interval, 2)
        if current_rps > self.peak_rps:
            self.peak_rps = current_rps

        # Latencies for this tick
        pcts = calculate_percentiles(self.tick_latencies, [50, 95, 99])
        avg_resp = round(sum(self.tick_latencies) / len(self.tick_latencies), 2) if self.tick_latencies else 0.0
        # Reset tick latencies
        self.tick_latencies = []

        failure_rate = round((failed_reqs / total_reqs * 100.0), 2) if total_reqs > 0 else 0.0

        tick_data = AggregatedMetricsTick(
            timestamp=now,
            elapsed_seconds=round(elapsed, 1),
            total_requests=total_reqs,
            requests_per_second=current_rps,
            avg_response_time=avg_resp,
            p50_response_time=pcts["p50"],
            p95_response_time=pcts["p95"],
            p99_response_time=pcts["p99"],
            failed_requests=failed_reqs,
            failure_rate=failure_rate,
            active_workers=active_workers,
            active_virtual_users=active_vu,
            socket_connections=socket_conns,
        )

        self.time_series.append(tick_data.model_dump())
        self.last_total_requests = total_reqs
        self.last_tick_time = now
        return tick_data

    def finalize(self) -> TestSummaryReport:
        self.end_time = time.time()
        duration = max(0.001, self.end_time - self.start_time)

        total_reqs = sum(w.total_requests for w in self.worker_metrics.values())
        failed_reqs = sum(w.failed_requests for w in self.worker_metrics.values())
        avg_rps = round(total_reqs / duration, 2)
        failure_rate = round((failed_reqs / total_reqs * 100.0), 2) if total_reqs > 0 else 0.0

        pcts = calculate_percentiles(self.all_latencies, [50, 95, 99])
        avg_latency = round(sum(self.all_latencies) / len(self.all_latencies), 2) if self.all_latencies else 0.0

        worker_summaries = {}
        for wid, w in self.worker_metrics.items():
            worker_summaries[wid] = {
                "total_requests": w.total_requests,
                "failed_requests": w.failed_requests,
                "active_vu": w.active_vu,
                "socket_connected": w.socket_connected,
                "errors": w.errors,
            }

        start_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.start_time))
        end_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.end_time))

        report = TestSummaryReport(
            test_id=self.test_id,
            target_url=self.target_url,
            mode=self.mode,
            exam_id=self.exam_id,
            start_time=start_str,
            end_time=end_str,
            duration_seconds=round(duration, 2),
            total_workers=len(self.worker_metrics),
            total_requests=total_reqs,
            failed_requests=failed_reqs,
            average_rps=avg_rps,
            peak_rps=round(self.peak_rps, 2),
            avg_latency_ms=avg_latency,
            p50_latency_ms=pcts["p50"],
            p95_latency_ms=pcts["p95"],
            p99_latency_ms=pcts["p99"],
            failure_rate=failure_rate,
            worker_summaries=worker_summaries,
            time_series=self.time_series,
        )

        self.save_reports(report)
        return report

    def save_reports(self, report: TestSummaryReport):
        html_content = self.generate_html_report(report)
        csv_content = self.generate_csv_report(report)

        html_path = REPORTS_DIR / f"{self.test_id}.html"
        csv_path = REPORTS_DIR / f"{self.test_id}.csv"

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        logger.info(f"Reports saved to {html_path} and {csv_path}")

    def generate_csv_report(self, report: TestSummaryReport) -> str:
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["=== Test Summary ==="])
        writer.writerow(["Test ID", report.test_id])
        writer.writerow(["Target URL", report.target_url])
        writer.writerow(["Mode", report.mode])
        writer.writerow(["Exam ID", report.exam_id])
        writer.writerow(["Start Time", report.start_time])
        writer.writerow(["End Time", report.end_time])
        writer.writerow(["Duration (s)", report.duration_seconds])
        writer.writerow(["Total Workers", report.total_workers])
        writer.writerow(["Total Requests", report.total_requests])
        writer.writerow(["Failed Requests", report.failed_requests])
        writer.writerow(["Failure Rate (%)", report.failure_rate])
        writer.writerow(["Avg RPS", report.average_rps])
        writer.writerow(["Peak RPS", report.peak_rps])
        writer.writerow(["Avg Latency (ms)", report.avg_latency_ms])
        writer.writerow(["P50 Latency (ms)", report.p50_latency_ms])
        writer.writerow(["P95 Latency (ms)", report.p95_latency_ms])
        writer.writerow(["P99 Latency (ms)", report.p99_latency_ms])
        writer.writerow([])

        writer.writerow(["=== Time Series Data ==="])
        writer.writerow([
            "Elapsed (s)",
            "Total Requests",
            "RPS",
            "Avg Response Time (ms)",
            "P50 (ms)",
            "P95 (ms)",
            "P99 (ms)",
            "Failed Requests",
            "Failure Rate (%)",
            "Active Workers",
            "Active VUs",
            "Socket.IO Connections",
        ])

        for row in report.time_series:
            writer.writerow([
                row.get("elapsed_seconds", 0),
                row.get("total_requests", 0),
                row.get("requests_per_second", 0),
                row.get("avg_response_time", 0),
                row.get("p50_response_time", 0),
                row.get("p95_response_time", 0),
                row.get("p99_response_time", 0),
                row.get("failed_requests", 0),
                row.get("failure_rate", 0),
                row.get("active_workers", 0),
                row.get("active_virtual_users", 0),
                row.get("socket_connections", 0),
            ])

        return output.getvalue()

    def generate_html_report(self, report: TestSummaryReport) -> str:
        time_labels = [row.get("elapsed_seconds", 0) for row in report.time_series]
        rps_data = [row.get("requests_per_second", 0) for row in report.time_series]
        p95_data = [row.get("p95_response_time", 0) for row in report.time_series]
        vu_data = [row.get("active_virtual_users", 0) for row in report.time_series]
        socket_data = [row.get("socket_connections", 0) for row in report.time_series]

        workers_rows = ""
        for wid, data in report.worker_summaries.items():
            workers_rows += f"""
            <tr>
                <td>{wid}</td>
                <td>{data.get('total_requests', 0):,}</td>
                <td>{data.get('failed_requests', 0):,}</td>
                <td>{data.get('active_vu', 0)}</td>
                <td>{data.get('socket_connected', 0)}</td>
            </tr>
            """

        html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <title>Load Test Report - {report.test_id}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #f8fafc; color: #1e293b; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }}
        h1 {{ margin-top: 0; color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 12px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #f1f5f9; padding: 16px; border-radius: 6px; border-left: 4px solid #3b82f6; }}
        .card-label {{ font-size: 0.85rem; color: #64748b; margin-bottom: 4px; }}
        .card-value {{ font-size: 1.5rem; font-weight: bold; color: #0f172a; }}
        .chart-container {{ position: relative; height: 320px; margin-bottom: 30px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
        th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ background: #f8fafc; font-weight: 600; color: #475569; }}
        .tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 600; background: #e2e8f0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 分散式 API 壓力測試報告</h1>
        <p><strong>Test ID:</strong> {report.test_id} | <strong>模式:</strong> <span class="tag">{report.mode}</span> | <strong>目標 API:</strong> {report.target_url} | <strong>試卷:</strong> {report.exam_id}</p>
        <p><strong>期間:</strong> {report.start_time} ~ {report.end_time} ({report.duration_seconds} 秒)</p>

        <div class="grid">
            <div class="card">
                <div class="card-label">總請求數 (Total Requests)</div>
                <div class="card-value">{report.total_requests:,}</div>
            </div>
            <div class="card" style="border-left-color: #10b981;">
                <div class="card-label">平均 RPS / 峰值 RPS</div>
                <div class="card-value">{report.average_rps} / {report.peak_rps}</div>
            </div>
            <div class="card" style="border-left-color: #6366f1;">
                <div class="card-label">平均延遲 (Avg Latency)</div>
                <div class="card-value">{report.avg_latency_ms} ms</div>
            </div>
            <div class="card" style="border-left-color: #8b5cf6;">
                <div class="card-label">P50 / P95 / P99 延遲</div>
                <div class="card-value">{report.p50_latency_ms} / {report.p95_latency_ms} / {report.p99_latency_ms} ms</div>
            </div>
            <div class="card" style="border-left-color: #ef4444;">
                <div class="card-label">失敗請求 / 錯誤率</div>
                <div class="card-value">{report.failed_requests:,} ({report.failure_rate}%)</div>
            </div>
            <div class="card" style="border-left-color: #f59e0b;">
                <div class="card-label">參與 Worker 數</div>
                <div class="card-value">{report.total_workers}</div>
            </div>
        </div>

        <h2>📈 效能趨勢圖表</h2>
        <div class="chart-container">
            <canvas id="rpsChart"></canvas>
        </div>
        <div class="chart-container">
            <canvas id="latencyChart"></canvas>
        </div>

        <h2>💻 各 Worker 統計</h2>
        <table>
            <thead>
                <tr>
                    <th>Worker ID</th>
                    <th>總請求數</th>
                    <th>失敗請求數</th>
                    <th>活躍 VU</th>
                    <th>Socket.IO 連線</th>
                </tr>
            </thead>
            <tbody>
                {workers_rows}
            </tbody>
        </table>
    </div>

    <script>
        const timeLabels = {json.dumps(time_labels)};
        
        // RPS & VU Chart
        new Chart(document.getElementById('rpsChart'), {{
            type: 'line',
            data: {{
                labels: timeLabels,
                datasets: [
                    {{
                        label: 'Requests Per Second (RPS)',
                        data: {json.dumps(rps_data)},
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.1)',
                        fill: true,
                        yAxisID: 'y'
                    }},
                    {{
                        label: 'Active Virtual Users',
                        data: {json.dumps(vu_data)},
                        borderColor: '#6366f1',
                        borderDash: [5, 5],
                        fill: false,
                        yAxisID: 'y1'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{ title: {{ display: true, text: '測試經過時間 (秒)' }} }},
                    y: {{ title: {{ display: true, text: 'RPS' }}, beginAtZero: true }},
                    y1: {{ position: 'right', title: {{ display: true, text: 'VUs' }}, grid: {{ drawOnChartArea: false }}, beginAtZero: true }}
                }}
            }}
        }});

        // Latency & Socket.IO Chart
        new Chart(document.getElementById('latencyChart'), {{
            type: 'line',
            data: {{
                labels: timeLabels,
                datasets: [
                    {{
                        label: 'P95 Latency (ms)',
                        data: {json.dumps(p95_data)},
                        borderColor: '#f59e0b',
                        backgroundColor: 'rgba(245, 158, 11, 0.1)',
                        fill: true,
                        yAxisID: 'y'
                    }},
                    {{
                        label: 'Socket.IO Connections',
                        data: {json.dumps(socket_data)},
                        borderColor: '#06b6d4',
                        borderDash: [4, 4],
                        fill: false,
                        yAxisID: 'y1'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{ title: {{ display: true, text: '測試經過時間 (秒)' }} }},
                    y: {{ title: {{ display: true, text: 'P95 Latency (ms)' }}, beginAtZero: true }},
                    y1: {{ position: 'right', title: {{ display: true, text: 'Socket.IO 連線數' }}, grid: {{ drawOnChartArea: false }}, beginAtZero: true }}
                }}
            }}
        }});
    </script>
</body>
</html>
        """
        return html
