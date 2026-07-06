# 快速使用指南

## 這是什麼

`multi_agent_claude_code` 是一套給 Ubuntu 公司環境使用的 Claude Code 多代理人 orchestration 範例。

它的重點是：

- 主 agent 不直接吃大上下文。
- 任務先 meeting，再拆成小 job。
- 每個 subagent 可以套用 strict/custom profile。
- 每個結果都要有 claim/evidence，不只回短 summary。
- reviewer 擋 false success。
- watchdog 偵測 timeout、missing status、reviewer missing、final verify fail。
- dashboard 顯示 run、agent state、failure、profile governance。

## 安裝 skill

把 release bundle 還原後，在你的專案根目錄執行：

```bash
mkdir -p .claude/skills
cp -R multi_agent_claude_code_release/.claude/skills/research-task-orchestrator .claude/skills/
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

## 跑 demo

Mock 模式：

```bash
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

Live 模式，前提是 Claude Code Router 已可用：

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

## 開 dashboard

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

打開：

```text
http://127.0.0.1:5174
```

## 判斷是否成功

不要只看 agent 是否有回文字。至少檢查：

- `task_harness_report.json`
- `artifact_verify_report.json`
- `reviewer_verdicts.json`
- `subagent_claim_ledger.json`
- `watchdog_report.json`
- dashboard 的 Current run / Agent board / Trustworthiness / Watchdog

如果 final artifact 沒過、reviewer 擋下、watchdog escalated，都不能算成功。
