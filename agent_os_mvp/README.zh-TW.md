# Agent OS MVP Dashboard 使用說明

這一包是給 `Claude Code + router + 開源 LLM` 工作流使用的輕量監控儀表板。

用途不是取代 `claude` 或 `ccr`，而是把既有任務執行結果整理成網頁：

- 最新 run 狀態
- meeting 結論
- agent 分工
- overflow / router error 告警
- artifact verify 結果

## 適用情境

如果你公司已經有：

- Ubuntu 環境
- Node.js / npm
- Python 3
- Claude Code
- Claude Code Router
- 開源模型服務或 OpenAI-compatible router upstream

這個 dashboard 可以直接疊上去，不需要改你既有 router 邏輯。

## 目錄說明

- `backend/`
  - FastAPI API
  - 讀取 `results/ai_company_task_harness` 內的 run artifact
  - 用 SQLite 整理成 dashboard 可讀格式
- `frontend/`
  - React + Vite 單頁網頁
- `start-dashboard.sh`
  - Linux / Ubuntu 啟動腳本
- `start-dashboard.cmd`
  - Windows 雙擊入口

## Ubuntu 安裝與啟動

### 1. 安裝 Claude Code 與 router

如果公司環境尚未安裝，可參考：

```bash
npm install -g @anthropic-ai/claude-code
npm install -g @musistudio/claude-code-router
```

確認版本：

```bash
claude --version
ccr version
node -v
npm -v
python3 --version
```

### 2. 安裝 dashboard backend

```bash
cd agent_os_mvp/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 安裝 dashboard frontend

```bash
cd ../frontend
npm install
```

### 4. 啟動 dashboard

```bash
cd ..
chmod +x start-dashboard.sh stop-dashboard.sh
./start-dashboard.sh
```

啟動後查看：

- Backend: `http://127.0.0.1:8010/health`
- Frontend: `http://127.0.0.1:5174/`

停止：

```bash
./stop-dashboard.sh
```

## Windows 啟動

如果你像這台電腦一樣要在 Windows 用：

1. 雙擊 `start-dashboard.cmd`
2. 開瀏覽器到 `http://127.0.0.1:5174/`

如果 PowerShell policy 會擋 `.ps1`，請優先用 `.cmd`。

## 與 Claude + Router 的搭配方式

建議流程：

1. 用既有 `Claude Code + router` 執行任務
2. 任務產出 run artifact 到：
   - `results/ai_company_task_harness/...`
3. dashboard backend 自動掃描這些結果
4. 前端顯示：
   - latest run
   - active agents
   - meeting log
   - summary 與 artifact checks

## 網頁建置方式

開發模式：

```bash
cd frontend
VITE_API_BASE_URL=http://127.0.0.1:8010 npm run dev -- --host 127.0.0.1 --port 5174
```

正式 build：

```bash
cd frontend
npm run build
```

build 輸出在：

- `frontend/dist/`

如果你要用 nginx 或內網靜態網站服務，只要把 `dist/` 發出去，並確保 API 指到 backend。

## 公司環境最小檢查清單

- `claude --version` 可執行
- `ccr version` 可執行
- `python3 -m uvicorn` 可啟動 backend
- `npm run dev` 或 `npm run build` 可執行
- `results/ai_company_task_harness` 內有 run artifact
- `curl http://127.0.0.1:8010/health` 回 `{"status":"ok"}`
- 打開 `http://127.0.0.1:5174/` 看得到畫面

## 注意事項

- 不要把 `backend/.venv`、`frontend/node_modules`、`logs` 上傳到 git
- 這個 dashboard 本身不會下載模型
- 這個 dashboard 本身不會修改你的 router 設定
- 它只負責讀取結果並視覺化
