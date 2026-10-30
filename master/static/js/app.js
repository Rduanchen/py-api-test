// Master Web UI Dashboard JavaScript
let ws = null;
let liveRpsChart = null;
let liveLatencyChart = null;

const chartData = {
    labels: [],
    rps: [],
    vu: [],
    p95: [],
    sockets: []
};

document.addEventListener("DOMContentLoaded", () => {
    initCharts();
    initWebSocket();
    loadExams();
    loadHistory();

    document.getElementById("btnStartTest").addEventListener("click", handleStartTest);
    document.getElementById("btnStopTest").addEventListener("click", handleStopTest);

    // Update relative heartbeat timestamps every second
    setInterval(updateHeartbeatTimestamps, 1000);
});

function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/dashboard`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        const badge = document.getElementById("wsStatusBadge");
        badge.textContent = "WS 在線";
        badge.className = "status-badge online";
    };

    ws.onclose = () => {
        const badge = document.getElementById("wsStatusBadge");
        badge.textContent = "WS 離線重試中...";
        badge.className = "status-badge offline";
        setTimeout(initWebSocket, 2000);
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleWsMessage(msg);
        } catch (e) {
            console.error("Failed to parse WS message:", e);
        }
    };
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case "DASHBOARD_INIT":
            updateWorkerCounts(msg.counts);
            renderWorkersTable(msg.workers);
            updateTestState(msg.test_state, msg.current_test_id);
            if (msg.exams) renderExamsDropdown(msg.exams);
            break;

        case "WORKER_STATUS_CHANGED":
            updateWorkerCounts(msg.counts);
            renderWorkersTable(msg.workers);
            break;

        case "TEST_STATE_CHANGED":
            updateTestState(msg.state, msg.test_id);
            if (msg.state === "COMPLETED") {
                showReportDownloadButtons(msg.test_id);
                loadHistory();
            }
            break;

        case "METRICS_TICK":
            handleMetricsTick(msg.data);
            break;
    }
}

function updateWorkerCounts(counts) {
    if (!counts) return;
    document.getElementById("statTotalWorkers").textContent = counts.total || 0;
    document.getElementById("statOnlineWorkers").textContent = counts.online || 0;
    document.getElementById("statRunningWorkers").textContent = counts.running || 0;
    document.getElementById("statOfflineWorkers").textContent = counts.offline || 0;
}

let cachedWorkers = [];

function renderWorkersTable(workers) {
    cachedWorkers = workers || [];
    const tbody = document.getElementById("workersTableBody");
    if (!workers || workers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">等待 Slave 註冊連線...</td></tr>`;
        return;
    }

    tbody.innerHTML = workers.map(w => {
        const statusClass = w.status.toLowerCase();
        const accountsCount = (w.assigned_accounts || []).length;
        const accountsPreview = accountsCount > 0 
            ? `<span class="tag" title="${w.assigned_accounts.map(a => a.test_id).join(', ')}">${accountsCount} 個帳號</span>`
            : `<span style="color: var(--text-muted); font-size: 0.8rem;">未分配</span>`;

        return `
            <tr data-worker-id="${w.worker_id}">
                <td><strong>${w.worker_id}</strong></td>
                <td>${w.ip} <small style="color: var(--text-muted);">(${w.remote_ip})</small></td>
                <td>${w.hostname}</td>
                <td><span class="status-badge ${statusClass}">${w.status}</span></td>
                <td>${w.cpu_cores} 核 (${Math.round(w.metrics?.cpu_percent || 0)}%)</td>
                <td class="last-seen" data-timestamp="${w.last_heartbeat}">計算中...</td>
                <td>${accountsPreview}</td>
            </tr>
        `;
    }).join("");

    updateHeartbeatTimestamps();
}

function updateHeartbeatTimestamps() {
    const now = Date.now() / 1000;
    document.querySelectorAll(".last-seen").forEach(el => {
        const ts = parseFloat(el.getAttribute("data-timestamp"));
        if (!ts || isNaN(ts)) {
            el.textContent = "無心跳";
            return;
        }
        const diff = Math.max(0, Math.round(now - ts));
        el.textContent = `${diff} 秒前`;
    });
}

function updateTestState(state, testId) {
    const badge = document.getElementById("testStateBadge");
    const btnStart = document.getElementById("btnStartTest");
    const btnStop = document.getElementById("btnStopTest");

    badge.textContent = `狀態: ${state}`;
    badge.className = `status-badge ${state.toLowerCase()}`;

    if (state === "RUNNING" || state === "PREPARING") {
        btnStart.disabled = true;
        btnStop.disabled = false;
    } else {
        btnStart.disabled = false;
        btnStop.disabled = true;
    }
}

function handleMetricsTick(data) {
    document.getElementById("metricTotalReqs").textContent = data.total_requests.toLocaleString();
    document.getElementById("metricRps").textContent = data.requests_per_second;
    document.getElementById("metricLatency").textContent = `${data.avg_response_time} / ${data.p95_response_time} ms`;
    document.getElementById("metricErrors").textContent = `${data.failed_requests.toLocaleString()} (${data.failure_rate}%)`;
    document.getElementById("metricActiveConns").textContent = `${data.active_virtual_users} / ${data.socket_connections}`;

    // Append to charts
    chartData.labels.push(`${data.elapsed_seconds}s`);
    chartData.rps.push(data.requests_per_second);
    chartData.vu.push(data.active_virtual_users);
    chartData.p95.push(data.p95_response_time);
    chartData.sockets.push(data.socket_connections);

    if (chartData.labels.length > 60) {
        chartData.labels.shift();
        chartData.rps.shift();
        chartData.vu.shift();
        chartData.p95.shift();
        chartData.sockets.shift();
    }

    liveRpsChart.update();
    liveLatencyChart.update();
}

function initCharts() {
    const ctxRps = document.getElementById("liveRpsChart").getContext("2d");
    liveRpsChart = new Chart(ctxRps, {
        type: "line",
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: "即時 RPS",
                    data: chartData.rps,
                    borderColor: "#10b981",
                    backgroundColor: "rgba(16, 185, 129, 0.1)",
                    fill: true,
                    tension: 0.2,
                    yAxisID: "y"
                },
                {
                    label: "活躍 VU",
                    data: chartData.vu,
                    borderColor: "#6366f1",
                    borderDash: [4, 4],
                    tension: 0.2,
                    yAxisID: "y1"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, title: { display: true, text: "RPS" } },
                y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "VU 數" } }
            }
        }
    });

    const ctxLatency = document.getElementById("liveLatencyChart").getContext("2d");
    liveLatencyChart = new Chart(ctxLatency, {
        type: "line",
        data: {
            labels: chartData.labels,
            datasets: [
                {
                    label: "P95 延遲 (ms)",
                    data: chartData.p95,
                    borderColor: "#f59e0b",
                    backgroundColor: "rgba(245, 158, 11, 0.1)",
                    fill: true,
                    tension: 0.2,
                    yAxisID: "y"
                },
                {
                    label: "Socket.IO 連線",
                    data: chartData.sockets,
                    borderColor: "#06b6d4",
                    borderDash: [3, 3],
                    tension: 0.2,
                    yAxisID: "y1"
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true, title: { display: true, text: "ms" } },
                y1: { beginAtZero: true, position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "連線數" } }
            }
        }
    });
}

function resetCharts() {
    chartData.labels.length = 0;
    chartData.rps.length = 0;
    chartData.vu.length = 0;
    chartData.p95.length = 0;
    chartData.sockets.length = 0;
    liveRpsChart.update();
    liveLatencyChart.update();
}

async function loadExams() {
    try {
        const res = await fetch("/api/exams");
        const data = await res.json();
        renderExamsDropdown(data.exams || []);
    } catch (e) {
        console.error("Failed to load exams:", e);
    }
}

function renderExamsDropdown(exams) {
    const sel = document.getElementById("examId");
    if (!exams || exams.length === 0) {
        sel.innerHTML = `<option value="">(無可用試卷)</option>`;
        return;
    }
    sel.innerHTML = exams.map(e => `
        <option value="${e.exam_id}">
            ${e.title} (${e.total_users} 考生 / ${e.total_puzzles} 題)
        </option>
    `).join("");
}

async function handleStartTest() {
    const targetUrl = document.getElementById("targetUrl").value.trim();
    const mode = document.getElementById("testMode").value;
    const examId = document.getElementById("examId").value;
    const totalUsers = parseInt(document.getElementById("totalUsers").value, 10);
    const spawnRate = parseFloat(document.getElementById("spawnRate").value);
    const duration = parseInt(document.getElementById("duration").value, 10);

    if (!targetUrl) {
        alert("請輸入受測後端 API 網址！");
        return;
    }

    resetCharts();
    document.getElementById("reportActions").style.display = "none";

    try {
        const res = await fetch("/api/test/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_url: targetUrl,
                mode: mode,
                exam_id: examId,
                total_users: totalUsers,
                spawn_rate: spawnRate,
                duration: duration
            })
        });

        const data = await res.json();
        if (!res.ok) {
            alert(`啟動失敗: ${data.detail || "未知錯誤"}`);
        } else {
            console.log("Test started:", data);
        }
    } catch (e) {
        alert(`發送啟動請求失敗: ${e.message}`);
    }
}

async function handleStopTest() {
    try {
        const res = await fetch("/api/test/stop", { method: "POST" });
        const data = await res.json();
        console.log("Test stopped:", data);
    } catch (e) {
        alert(`發送停止請求失敗: ${e.message}`);
    }
}

function showReportDownloadButtons(testId) {
    if (!testId) return;
    const container = document.getElementById("reportActions");
    const btnHtml = document.getElementById("btnDownloadHtml");
    const btnCsv = document.getElementById("btnDownloadCsv");

    btnHtml.href = `/api/reports/${testId}.html`;
    btnCsv.href = `/api/reports/${testId}.csv`;
    container.style.display = "flex";
}

async function loadHistory() {
    try {
        const res = await fetch("/api/test/history");
        const data = await res.json();
        renderHistoryTable(data.tests || []);
    } catch (e) {
        console.error("Failed to load history:", e);
    }
}

function renderHistoryTable(tests) {
    const tbody = document.getElementById("testHistoryBody");
    if (!tests || tests.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted);">暫無歷史紀錄</td></tr>`;
        return;
    }

    tbody.innerHTML = tests.map(t => {
        const summary = t.summary || {};
        const totalReq = (summary.total_requests || 0).toLocaleString();
        const avgRps = summary.average_rps || 0;
        return `
            <tr>
                <td><strong>${t.test_id}</strong></td>
                <td>${t.target_url}</td>
                <td><span class="tag">${t.mode}</span></td>
                <td>${t.exam_id}</td>
                <td>${totalReq}</td>
                <td>${avgRps}</td>
                <td><span class="status-badge ${t.state.toLowerCase()}">${t.state}</span></td>
                <td>${t.start_time || ""}</td>
                <td>
                    <a href="/api/reports/${t.test_id}.html" target="_blank" class="tag" style="text-decoration:none; color:var(--primary);">HTML</a>
                    <a href="/api/reports/${t.test_id}.csv" target="_blank" class="tag" style="text-decoration:none; color:var(--primary);">CSV</a>
                </td>
            </tr>
        `;
    }).join("");
}
