# Distributed API Load Testing System (Python 版本)

專為 **NTUT Exam Host 考試系統** 設計的分散式 API 壓力與負載測試系統，採用 **Master–Slave 主從式架構**。

適用於同一個 LAN 區域網路內，由 1 台 Master 伺服器集中調度並管理多達 40+ 台 Slave 實體測試機，同時向受測 API 伺服器發送高併發負載與維持 Socket.IO 長連線。

---

## 🌟 系統亮點

1. **主從式架構 (Master-Slave)**：
   - **Master**：集中控制中心，提供極簡 Web UI、動態學生帳號分配、心跳偵測、命令廣播與即時數據聚合。
   - **Slave**：實際發送請求的節點，基於 **Locust** 負載引擎與原生 Python `cryptography`，不需依賴 Node.js 或複雜 Docker。
2. **兩種測試維度 (Two Test Modes)**：
   - 🎓 **實際測試 (Actual Test - 需要登入帳號)**：
     - Master 自動解析 `./test-config` 中的考試試卷與真實考生帳號（`accessibleUsers`）。
     - Slave 接收 Master 指派的學生帳號，使用原生 Python 執行 RSA-OAEP 金鑰交換、AES-256-GCM 裝置註冊與登入。
     - 建立並維持 `/user` Namespace 的 **Socket.IO 長連線**（監聽訊息、註冊狀態）。
     - 根據題庫答案程式碼定期發送解題上傳、成績回報、作答紀錄與心跳日誌。
   - ⚡ **跳過帳號註冊 (Pure Simulation Mode)**：
     - 單台 Slave 電腦即可同時運行數十到數百個虛擬使用者（VU）。
     - 配合後端 `LOAD_TEST_MODE=pure` 或 Header `x-test-fake-ip`，略過加解密負擔，測試後端 API 與資料庫極限吞吐量。
3. **即時視覺化中控台 (Master Web UI)**：
   - 純 HTML / CSS / Vanilla JavaScript 打造，免編譯打包。
   - **Section A: Worker Overview**：即時顯示總機器數、在線、執行中與離線機器，表格即時更新每台 Slave 之 IP、Hostname、CPU 與心跳。
   - **Section B: Test Configuration**：受測後端網址（可自由修改）、模式切換、試卷選擇、總使用者數、每秒生成率、測試時長。
   - **Section C: Test Results**：即時 RPS、平均延遲、P50 / P95 / P99 延遲、錯誤率、Socket.IO 連線數折線圖，並可一鍵下載 HTML / CSV 報表。

---

## 📁 專案目錄結構

```text
py-api-test/
├── master/
│   ├── main.py              # FastAPI 服務入口 (REST API, WebSocket, Web UI)
│   ├── registry.py          # Worker 註冊中心、Heartbeat 巡檢 (5s心跳, 15s逾時)
│   ├── account_manager.py   # 解析 ../test-config 題庫並分配學生帳號
│   ├── scheduler.py         # 測試狀態機 (IDLE, PREPARING, RUNNING, STOPPING, etc.)
│   ├── statistics.py        # 數據聚合、延遲百分位數計算、HTML/CSV 報告生成
│   ├── database.py          # SQLite 本地紀錄儲存
│   └── static/              # Web UI (index.html, style.css, app.js)
├── slave/
│   ├── main.py              # Slave Worker 啟動入口
│   ├── client.py            # Master WebSocket 客戶端 (自動註冊、心跳、命令監聽)
│   ├── runner.py            # Locust 負載執行調度器
│   ├── socketio_worker.py   # Socket.IO 長連線客戶端 (/user namespace)
│   ├── crypto_helper.py     # 原生 Python RSA-OAEP + AES-256-GCM 加解密模組
│   └── tasks/
│       ├── actual_user.py   # 實際學生行為流程
│       └── pure_user.py     # 跳過註冊高併發流程
├── shared/
│   ├── models.py            # Pydantic 共同資料模型
│   └── protocol.py          # 通訊協定與狀態常數
├── tests/                   # 自動化測試套件 (pytest)
├── reports/                 # 測試報告輸出目錄
├── setup_and_run_slave.sh   # 一鍵安裝與啟動 Slave 腳本
├── run_master.sh            # 啟動 Master 腳本
├── run_simulated_slaves.sh  # 單機模擬多個 Slave 腳本
├── requirements.txt         # Python 套件依賴
└── README.md
```

---

## 🚀 快速上手指南

### 1. 啟動 Master 伺服器

#### Linux / macOS:
```bash
cd py-api-test
./run_master.sh
```

預設會啟動在 `http://0.0.0.0:8000`。
使用瀏覽器打開 `http://localhost:8000` 即可進入 Web 控制台。

---

### 2. 在 Slave 實體測試機上一鍵安裝與啟動

系統提供了一鍵腳本 `setup_and_run_slave.sh`，會自動檢查 Python 環境、建立虛擬環境、安裝依賴套件並連線至指定的 Master。

#### Linux / macOS:
```bash
cd py-api-test

# 將 http://192.168.1.10:8000 替換為實際 Master 電腦的 IP 與 Port
./setup_and_run_slave.sh http://192.168.1.10:8000
```

#### Windows:
在 Command Prompt (CMD) 或 PowerShell 中：
```cmd
cd py-api-test
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

:: 啟動 Slave 並指定 Master IP
python -m slave.main --master http://192.168.1.10:8000
```

> **提示**：Slave 啟動後會自動向 Master 註冊並維持每 5 秒一次的心跳。即使 Master 尚未啟動或中途重啟，Slave 也會以指數退避（Exponential Backoff）持續重連，不會崩潰退出。

---

### 3. 單機模擬測試 (在同一台電腦模擬 5 ~ 20 台 Slave)

如果手邊只有一台開發電腦，可以使用 `run_simulated_slaves.sh` 在本地啟動多台模擬 Slave：

```bash
cd py-api-test

# 在本地啟動 5 台虛擬 Slave 節點連線至本地 Master
./run_simulated_slaves.sh 5 http://127.0.0.1:8000
```

啟動後，打開 Master Web UI (`http://localhost:8000`) 即可在表格上看見 5 台 Slave 同時在線！

---

## 🕹️ Web UI 測試操作流程

1. **檢查 Slave 狀態**：
   - 確認上方卡片顯示在線機器數量（如 5 台）。
   - 下方表格列出每台機器的 Worker ID、來源 IP、Hostname 與心跳更新時間。
2. **設定測試參數**：
   - **受測後端網址 (Target API URL)**：輸入受測的 `host` User 服務網址，例如 `http://192.168.1.100:3001`。
   - **測試模式**：
     - 若要測試真實考生流程與加解密，請選「**實際測試 (真實學生帳號登入 + 加密 + Socket.io)**」。
     - 若要測試伺服器極限併發，請選「**跳過帳號註冊 (極限高併發 / 虛擬使用者)**」。
   - **試卷選擇**：從下拉選單選擇試卷（例如 `第一卷 (Python)`），系統會自動配對題目與學生帳號。
   - **總虛擬使用者數 / 生成率 / 持續時間**。
3. **點擊「開始測試 (Start Test)」**：
   - Master 會向所有在線 Slave 派發準備命令與指派帳號。
   - 所有 Slave 回報 Ready 後，同步開始產生負載與連線。
   - 折線圖每秒更新即時 RPS、延遲與 Socket.IO 連線數。
4. **測試完成 / 停止**：
   - 達到設定時間後自動停止（或點擊「停止測試」手動急停）。
   - 畫面會跳出「**下載 HTML 報告**」與「**下載 CSV 數據**」按鈕，可直接下載或查看歷史紀錄。

---

## 🧪 執行自動化測試

本專案內建完整的自動化單元與整合測試套件：

```bash
cd py-api-test
source .venv/bin/activate
PYTHONPATH=. pytest -v tests/
```

測試涵蓋項目：
- ✅ 原生 RSA-OAEP 金鑰交換與 AES-256-GCM 加解密往返
- ✅ Master REST API 與試卷讀取
- ✅ Slave 註冊、Heartbeat 巡檢與離線判斷
- ✅ 考生帳號切分與分配演算法
- ✅ 延遲百分位數（P50, P95, P99）統計直方圖計算
