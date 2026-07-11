# Live Model Service Setup

這份文件只適用於 live mode。mock demo 不需要模型檔、不需要 Ollama、不需要 LM Studio、不需要 Claude Code Router，也不需要 API key。

## 核心原則

`multi_agent_claude_code` 不直接讀取本機模型檔。repo 只連到你已經啟動的 model service endpoint，例如 Ollama、LM Studio，或其他 OpenAI-compatible server。

如果模型檔已搬到 Windows `D:` 槽，請在模型服務本身完成設定。repo 不應提交真實模型路徑、API key、password、SSH key 或 model weights。

公司 live 驗收的 production gate 應以 Claude Code Router 或 Claude CLI 為主。raw provider completion 只作診斷，除非你明確設定 `AI_COMPANY_REQUIRE_DIRECT_PROVIDER_COMPLETION=1`。

## 建議驗證順序

1. 驗證 model service visibility：`/api/tags` 或 `/v1/models`。
2. 驗證 Claude Code Router `/health`。
3. 驗證 Claude Code Router `/v1/messages` 或 Claude CLI live turn。
4. 視需要診斷 raw provider completion：`/v1/chat/completions` 或 `/api/generate`。

`/health` 成功只代表 router process 活著，不代表 live agent execution 可以成功。一定要再測 router/Claude 的 live completion path。

## Ollama

Ollama 常見 endpoint：

```text
http://127.0.0.1:11434/api/tags
http://127.0.0.1:11434/v1/models
http://127.0.0.1:11434/v1/chat/completions
http://127.0.0.1:11434/api/generate
```

如果模型搬到 `D:` 槽，請先確認 Ollama 服務設定已指向新的模型位置。內部文件可以使用 `<D_DRIVE_MODEL_DIR>` 當占位符，不要把真實私人路徑提交到 repo。

檢查重點：

- `GET /api/tags` 能看到預期模型，只代表模型已被服務辨識。
- `GET /v1/models` 若你的 Ollama 版本支援 OpenAI-compatible API，也應可回應。
- `POST /v1/chat/completions` timeout 不一定代表 AI-company live path 必失敗；它可能是模型冷啟動、負載、GPU/CPU、`num_predict`、OpenAI-compatible schema 或 server log 問題。
- CCR provider 指向 Ollama 後，正式驗收仍要測 CCR `/v1/messages` 或 Claude CLI。

## LM Studio

LM Studio 常見 OpenAI-compatible endpoint：

```text
http://127.0.0.1:1234/v1/models
http://127.0.0.1:1234/v1/chat/completions
```

如果模型檔在 `D:` 槽，請在 LM Studio UI 或 server 設定中載入 `<D_DRIVE_MODEL_DIR>` 下的模型。repo 只需要 endpoint，不需要知道模型檔實際位置。

## Custom OpenAI-Compatible Server

自架 server 請提供 OpenAI-compatible API：

```text
GET /v1/models
POST /v1/chat/completions
```

若 direct completion timeout，但 router/Claude live gate 成功，AI-company runner 會把 direct timeout 記為 provider diagnostic warning，而不是把整個 run 判定為 live failure。

## Provider Diagnostic

可用以下命令產生診斷報告：

```bash
python3 scripts/probe_provider_diagnostic.py \
  --tags-url http://127.0.0.1:11434/api/tags \
  --models-url http://127.0.0.1:11434/v1/models \
  --chat-url http://127.0.0.1:11434/v1/chat/completions \
  --model '<model-name>' \
  --timeout-sec 20 \
  --output results/provider_diagnostic_report.json
```

若報告顯示 `MODEL_VISIBLE_BUT_COMPLETION_TIMEOUT`，也就是 `/api/tags` 可見模型但 `/v1/chat/completions timeout`，處理順序是：

1. 先確認 CCR `/v1/messages` 或 Claude CLI 是否可完成最小 prompt。
2. 再檢查 Ollama/LM Studio server logs、模型冷啟動、GPU/CPU 負載、context/output token 設定與 payload schema。
3. 只有在你需要 raw provider completion 作為正式驗收時，才設定 `AI_COMPANY_REQUIRE_DIRECT_PROVIDER_COMPLETION=1`。

## 與 Mock Demo 的關係

mock demo 不依賴任何模型位置。即使模型搬到 Windows `D:` 槽，以下命令仍應可以在 fresh clone 後成功：

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

只有 live mode 需要確認模型服務與 CCR。
