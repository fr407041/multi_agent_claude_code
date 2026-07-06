# Multi-Agent Claude Code

這個 repository 是一套給公司 Ubuntu 環境使用的 `Claude Code + Claude Code Router + open-source LLM` 多代理人工作流範例。

核心目標不是讓 agent 自由亂跑，而是讓任務可以被拆成小工作、分派給限制型 subagent、留下可追蹤 artifacts，並用 reviewer、watchdog、memory guard、dashboard 擋掉假成功、無限 loop、token overflow 與 router timeout。

## 你會得到什麼

- `research-task-orchestrator` Claude Code skill
- strict/custom subagent profiles
- AI-company meeting -> task assignment -> execution -> review -> verify 流程
- claim/evidence ledger，避免 subagent 只回短 summary 而漏資訊
- main-agent memory guard，避免主 agent 長任務累積上下文爆掉
- watchdog，偵測 missing status、timeout、reviewer missing、final verify fail
- bundled dashboard，可用網頁看 run、agent state、failure、profile governance
- release-readiness demo case，可驗證整套流程是否可信

## 最簡安裝方式

在公司 Ubuntu 專案根目錄中，放入本 repo 的 `.claude/skills/research-task-orchestrator`：

```bash
mkdir -p .claude/skills
cp -R path/to/multi_agent_claude_code/.claude/skills/research-task-orchestrator .claude/skills/
```

確認 Claude Code 可以看到 skill 後，可以直接用自然語言呼叫：

```text
Use the research-task-orchestrator skill to run this task with strict subagents: <你的任務>
```

或只先產生 strict-agent spec：

```text
Use the research-task-orchestrator skill to create a strict-agent spec for this task: <你的任務>
```

## 跑 demo

Demo case 是一個通用公司任務：判斷小型內部 dashboard 是否 release ready。

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

如果只是先確認流程與 artifacts，可用 mock：

```bash
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

## 開 dashboard

第一次使用 dashboard：

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
```

啟動：

```bash
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

瀏覽器開：

```text
http://127.0.0.1:5174
```

停止：

```bash
bash .claude/skills/research-task-orchestrator/scripts/stop_dashboard.sh
```

## 如何判讀結果

成功不只看 agent 有沒有回文字，至少要看：

- `task_harness_report.json` 的 `overall_status`
- `artifact_verify_report.json` 的 `all_passed`
- `reviewer_verdicts.json` 是否有 `FALSE_SUCCESS_BLOCKED` 或 `PROFILE_POLICY_VIOLATION`
- `subagent_claim_ledger.json` 是否每個 accepted claim 都有 evidence refs
- `watchdog_report.json` 是否 `healthy`，或是否 bounded escalation
- dashboard 的 Current run / Agent board / Trustworthiness / Watchdog

## 這次 demo 的真實結果

實際 live demo 有跑，但結論是 `NOT DELIVERABLE YET`：

- strict profiles、meeting、claim ledger、reviewer、watchdog、dashboard 都有留下 artifacts
- 本機 Ollama OpenAI-compatible endpoint 可回應
- 但 Claude Code Router `/v1/messages` 會 timeout
- watchdog 正確 escalated，沒有把 timeout 誤判成成功

完整報告：

```text
deliverables/strict_custom_subagents_demo_report.md
```

## 目錄重點

- `.claude/skills/research-task-orchestrator/`：公司端要安裝的 Claude Code skill
- `scripts/`：AI-company harness、meeting、execution、reviewer、watchdog、memory guard
- `configs/ai_company/`：profile、限制、KPI、schema
- `docs/ai_specs/`：demo spec
- `tests/fixtures/ai_company_release_readiness_demo/`：demo input files
- `deliverables/strict_custom_subagents_demo_report.md`：實測報告

## 注意

- 不包含 API key、密碼、公司資料、Docker image、模型權重。
- 公司端假設已經有 Claude Code、Claude Code Router、open-source LLM endpoint。
- 這套設計不承諾 Claude Code 2.1.x 有硬隔離 sandbox；strict mode 是 wrapper shaping + artifact verification + dashboard visibility。
