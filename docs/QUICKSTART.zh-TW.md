# 快速使用指南

## 這是什麼

`multi_agent_claude_code` 是一套給 Ubuntu 公司環境使用的 Claude Code 多代理人 orchestration 範例。

目前公開版定位是 mock-first smoke package：先確認 fresh clone 可以跑出可信 artifacts，不依賴 Docker、Claude Code Router、Ollama、API key 或任何 LLM model。

它的重點是：

- 主 agent 不直接吃大上下文。
- 任務先 meeting，再拆成小 job。
- 每個 subagent 可以套用 strict/custom profile。
- 每個結果都要有 claim/evidence，不只回短 summary。
- reviewer 擋 false success。
- watchdog 偵測 timeout、missing status、reviewer missing、final verify fail。

## 第一個必跑測試

在 repo 根目錄執行：

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

如果你的系統只有 `python`：

```bash
python scripts/verify_install.py
python scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

成功後會產生：

```text
results/ai_company_task_harness/<run-id>/
```

核心報告實際位置：

```text
results/ai_company_task_harness/<run-id>/ai_company/meeting_decision.json
results/ai_company_task_harness/<run-id>/ai_company/task_harness_report.json
results/ai_company_task_harness/<run-id>/ai_company/reviewer_verdicts.json
results/ai_company_task_harness/<run-id>/ai_company/artifact_verify_report.json
results/ai_company_task_harness/<run-id>/ai_company/subagent_claim_ledger.json
results/ai_company_task_harness/<run-id>/ai_company/watchdog_report.json
```

## 安裝 skill

在你的專案根目錄執行：

```bash
mkdir -p .claude/skills
cp -R .claude/skills/research-task-orchestrator .claude/skills/
```

## 呼叫方式

一般 strict subagents：

```text
Use the research-task-orchestrator skill to run this task with strict subagents: <你的任務>
```

只產生 spec：

```text
Use the research-task-orchestrator skill to create a strict-agent spec for this task: <你的任務>
```

檢查最新 run：

```text
Use the research-task-orchestrator skill to inspect profile violations in the latest run.
```

## Dashboard 狀態

目前公開版只有 dashboard source/helper placeholder，還不是可直接開網頁的完整 dashboard。

你可以執行下列命令檢查 dashboard placeholder 是否存在：

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

但目前不要期待 `http://127.0.0.1:5174` 會真的有 web UI。真正可跑的 dashboard 應另行實作。

## Live Router 狀態

目前公開版沒有 bundled live router runner。不要直接執行：

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

因為該檔案目前不在公開 checkout 中。

如果你要在公司環境測 live mode，請先自行確認：

1. Claude Code Router 已可用。
2. open-source LLM model service 已啟動。
3. 若模型已搬到 Windows `D:` 槽，請先確認 Ollama、LM Studio 或你的 model server 已指向新的模型位置。
4. 先測模型 endpoint：`/v1/models` 或 `/api/tags`。
5. 再測 CCR `/health`。
6. 最後一定要測 CCR `/v1/messages`，只測 `/health` 不代表 live agent 可用。

Mock demo 不依賴模型路徑，所以 D 槽模型不會影響第一個 smoke test。

## 判斷是否成功

不要只看 agent 是否有回文字。至少檢查：

- `ai_company/task_harness_report.json`
- `ai_company/artifact_verify_report.json`
- `ai_company/reviewer_verdicts.json`
- `ai_company/subagent_claim_ledger.json`
- `ai_company/watchdog_report.json`

如果 final artifact 沒過、reviewer 擋下、watchdog escalated，都不能算成功。
