# Multi-Agent Claude Code

Status:

- Runnable mock demo: yes
- Mock demo dependencies: no Docker, no Claude Code Router, no Ollama, no API key, no model service
- Live router demo: available through `scripts/run_common_research_with_router.sh`
- Dashboard web app: runnable FastAPI + React package under `agent_os_mvp`

This repository provides a company Ubuntu reference for an AI-company style `Claude Code + Claude Code Router + open-source LLM` workflow. The default path is mock-first so a fresh clone can verify orchestration artifacts before users configure live router, local model services, or dashboard runtime.

## First Run

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

A successful run writes artifacts under:

```text
results/ai_company_task_harness/<run-id>/ai_company/
```

Expected artifacts include `meeting_decision.json`, `task_harness_report.json`, `reviewer_verdicts.json`, `artifact_verify_report.json`, `subagent_claim_ledger.json`, `watchdog_report.json`, and `main_agent_memory_guard_report.json`.

## Live Router Mode

Live mode requires your existing Claude Code Router and an OpenAI-compatible open-source LLM endpoint. It still does not read model files directly from this repo.

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

The live runner fails fast in this order:

1. model endpoint: `MODEL_MODELS_URL` or `MODEL_TAGS_URL`
2. Claude Code Router `/health`
3. Claude Code Router `/v1/messages`
4. verified side-effect smoke: the model must produce the expected file content, not merely claim that it did

Useful environment overrides:

```bash
export MODEL_MODELS_URL=http://127.0.0.1:11434/v1/models
export MODEL_TAGS_URL=http://127.0.0.1:11434/api/tags
export CCR_BASE_URL=http://127.0.0.1:3456
export CCR_API_KEY=local-router-token
export CLAUDE_MODEL_ALIAS=ollama,qwen2.5-coder:3b
export CCR_PREFERRED_MODEL=qwen2.5-coder:3b
export CCR_MAX_OUTPUT_TOKENS=1024
```

For Claude Code Router, prefer comma provider syntax when selecting local Ollama models:

```bash
export CLAUDE_MODEL_ALIAS=ollama,qwen2.5-coder:7b
```

Slash syntax such as `ollama/qwen2.5-coder:7b` may not route as expected in all CCR setups.

The live runner performs a side-effect smoke check before the full live harness. The check is intentionally strict: the model must create the expected file content, not merely reply that it created it. If a qwen2.5 run returns a plausible success message without the verified file side effect, the run is blocked as `FAILED` instead of being counted as a successful AI-company execution.

Detailed D-drive and local model service setup is documented in [docs/LIVE_MODEL_SERVICE_SETUP.zh-TW.md](docs/LIVE_MODEL_SERVICE_SETUP.zh-TW.md). Moving models to Windows `D:` affects only the model service configuration, not mock mode.

## Dashboard

Install and start the bundled dashboard:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Run the dashboard smoke check:

```bash
bash agent_os_mvp/smoke-dashboard.sh
```

Open:

```text
http://127.0.0.1:5174
```

The dashboard is a localhost development runtime. If `8010` or `5174` is occupied by another service, stop that service or override ports:

```bash
AGENT_OS_BACKEND_PORT=8011 AGENT_OS_FRONTEND_PORT=5175 bash agent_os_mvp/smoke-dashboard.sh
```

Stop:

```bash
bash .claude/skills/research-task-orchestrator/scripts/stop_dashboard.sh
```

The dashboard reads run artifacts from `results/ai_company_task_harness` and shows All runs, Current run agent board, trustworthiness, failures by agent, profile governance, claim/evidence, memory guard, and watchdog state.

## Claude Code Skill

Install the skill into a company Ubuntu project:

```bash
mkdir -p .claude/skills
cp -R .claude/skills/research-task-orchestrator .claude/skills/
```

Then ask Claude Code:

```text
Use the research-task-orchestrator skill to run this task with strict subagents: <your task>
```

## Repository Contents

- `.claude/skills/research-task-orchestrator/`: Claude Code skill entry, role profiles, dashboard installer, dashboard assets
- `scripts/verify_install.py`: first-run checkout validation
- `scripts/run_ai_company_task_harness.py`: mock/live harness entrypoint
- `scripts/run_common_research_with_router.sh`: live router preflight + harness runner
- `scripts/smoke_live_tool_side_effect.py`: live side-effect guard for local open-source models
- `scripts/worker_claude_router*.sh`: repo-local live worker wrappers
- `docs/ai_specs/ai-company-release-readiness-strict-demo.json`: demo spec
- `tests/fixtures/ai_company_release_readiness_demo/`: bounded demo input evidence
- `agent_os_mvp/`: runnable dashboard package

## Safety

This repository should not contain API keys, passwords, Docker images, model weights, result caches, `.venv`, `node_modules`, SQLite runtime DBs, or logs.
