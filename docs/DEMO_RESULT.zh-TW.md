# Strict Custom Subagents Demo Result

## Demo 目標

驗證 `research-task-orchestrator` skill 是否能用 strict/custom subagents 跑出可追蹤的 AI-company workflow：

- meeting 收斂
- 任務分派
- strict agent profile metadata
- claim/evidence ledger
- reviewer gating
- memory guard
- watchdog
- dashboard visibility

Demo 任務：判斷一個小型內部 dashboard 是否 release ready。

## 實際結果

結論：`NOT DELIVERABLE YET`

不是因為 orchestration control plane 沒運作，而是 live Claude Code Router route timeout。

## 已通過的部分

- `meeting_status = MEETING_READY`
- `run_profile_mode = strict`
- meeting 1 round 收斂
- 任務分派給：
  - `research_agent`
  - `risk_reviewer`
  - `decision_agent`
  - `synthesis_agent`
- status artifacts 有保存：
  - `agent_profile`
  - `profile_mode`
  - `effective_policy_level`
  - allowed skills
  - allowed MCP groups
  - tool policy
- claim ledger 有建立，並可追到 status/raw/prompt evidence refs
- reviewer 沒有把 timeout 當成功
- watchdog 偵測 incomplete run 並 escalated
- failure drill 刪除 status 後，watchdog 可偵測 `MISSING_STATUS_FILE` 並補 fallback status

## 未通過的部分

所有 live child jobs 都 timeout：

| Job | Role | Status |
| --- | --- | --- |
| `job-001` | `research_agent` | `CHILD_TIMEOUT` |
| `job-002` | `risk_reviewer` | `CHILD_TIMEOUT` |
| `job-003` | `decision_agent` | `CHILD_TIMEOUT` |
| `job-004` | `synthesis_agent` | `CHILD_TIMEOUT` |

因此 final artifact 沒有產生，不能視為公司可交付版本。

## Router 診斷

本機 Ollama-compatible endpoint 可用：

- `/v1/models` 成功
- `qwen2.5-coder:3b` 直接 `/v1/chat/completions` 約 11 秒可回 OK
- `gemma3:4b` 約 56 秒可回 OK

但 Claude Code Router Anthropic-compatible endpoint timeout：

- `/health` 成功
- `/v1/messages` timeout
- Claude CLI via router timeout

所以目前主要 blocker 是 CCR `/v1/messages` route，而不是 strict subagent 設計本身。

## 下一步

先修 router，最低限度要讓 `/v1/messages` 在 30 秒內回 OK：

```bash
curl -sS http://127.0.0.1:3456/v1/messages \
  -H 'content-type: application/json' \
  -H 'x-api-key: local-test-key' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"qwen2.5-coder:3b","max_tokens":32,"messages":[{"role":"user","content":"Reply exactly OK"}]}'
```

重要：目前公開 smoke package 沒有 bundled live runner。不要從此公開 checkout 直接執行 `scripts/run_common_research_with_router.sh`，因為該檔案尚未加入。

Live rerun 由 GitHub issue #3 追蹤：等 real live router runner 補上並通過 `/v1/messages` preflight 後，再把 live command 放回 README/Quickstart。
