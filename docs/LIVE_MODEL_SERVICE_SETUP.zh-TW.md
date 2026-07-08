# Live Model Service Setup

這份文件只適用於 live mode。mock demo 不需要模型檔、不需要 Ollama、不需要 LM Studio、不需要 Claude Code Router，也不需要 API key。

## 核心原則

`multi_agent_claude_code` 不直接讀取本機模型檔。repo 只會連到你已經啟動的 model service endpoint，例如 Ollama、LM Studio，或其他 OpenAI-compatible server。

如果模型檔已搬到 Windows `D:` 槽，請在模型服務本身完成設定。repo 不應提交真實模型路徑、API key、password、SSH key、model weights。

## 驗證順序

live mode 請照這個順序驗證：

1. 驗證 model service endpoint。
2. 驗證 Claude Code Router `/health`。
3. 驗證 Claude Code Router `/v1/messages`。

`/health` 成功只代表 router process 活著，不代表 live agent execution 可以成功。一定要測 `/v1/messages`。

## Ollama

Ollama 常見 endpoint：

```text
http://127.0.0.1:11434/api/tags
http://127.0.0.1:11434/v1/models
```

如果模型搬到 `D:` 槽，請先確認 Ollama 服務設定已指向新的模型位置，例如使用 `<D_DRIVE_MODEL_DIR>` 作為內部文件中的占位符。不要把真實私人路徑提交到 repo。

檢查重點：

- `GET /api/tags` 能看到預期模型。
- `GET /v1/models` 若你的 Ollama 版本支援 OpenAI-compatible API，也應可回應。
- CCR provider 指向 Ollama 的 OpenAI-compatible base URL 時，要再測 CCR `/v1/messages`。

## LM Studio

LM Studio 常見 OpenAI-compatible endpoint：

```text
http://127.0.0.1:1234/v1/models
```

如果模型檔在 `D:` 槽，請在 LM Studio UI 或 server 設定中載入 `<D_DRIVE_MODEL_DIR>` 下的模型。repo 只需要知道 endpoint，不需要知道模型檔實際位置。

檢查重點：

- LM Studio server 已啟動。
- `GET /v1/models` 能看到載入模型。
- CCR `/v1/messages` 能透過該 endpoint 完成最小 prompt。

## Custom OpenAI-Compatible Server

自架 server 請提供 OpenAI-compatible API：

```text
GET /v1/models
POST /v1/chat/completions
```

如果模型檔放在 `D:` 槽，請在 server 啟動參數或 config 中使用 `<D_DRIVE_MODEL_DIR>`。repo 不應保存真實本機路徑。

檢查重點：

- `/v1/models` 回應正常。
- `/v1/chat/completions` 可完成最小請求。
- CCR `/v1/messages` 可完整回應，不只 `/health` 成功。

## CCR Live Preflight

建議順序：

```text
model endpoint /api/tags or /v1/models
CCR /health
CCR /v1/messages
```

判斷方式：

- model endpoint 失敗：先修 Ollama、LM Studio 或 custom server。
- CCR `/health` 失敗：先修 router process 或 router config。
- CCR `/health` 成功但 `/v1/messages` 失敗：live agent 仍不可用，常見原因是 provider/model mapping、timeout、context/output token 設定或 OpenAI-compatible schema 不一致。

## 與 Mock Demo 的關係

mock demo 不依賴任何模型位置。即使模型搬到 Windows `D:` 槽，以下命令仍應可以在 fresh clone 後成功：

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

只有 live mode 需要確認模型服務與 CCR。
