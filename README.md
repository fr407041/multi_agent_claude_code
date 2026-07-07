# Multi-Agent Claude Code

Status:

- Runnable mock demo: yes
- Mock demo dependencies: no Docker, no Claude Code Router, no Ollama, no API key, no model service
- Live router demo: not bundled in this public smoke package yet
- Dashboard web app: placeholder/check only in this public smoke package

This repository provides a company Ubuntu reference for an AI-company style `Claude Code + Claude Code Router + open-source LLM` workflow. The current public package is intentionally mock-first so a fresh clone can verify orchestration artifacts before users configure live router, local model services, or dashboard runtime.

## First Run

Run these commands after cloning:

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

If your system uses `python` instead of `python3`:

```bash
python scripts/verify_install.py
python scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

A successful run writes:

```text
results/ai_company_task_harness/<run-id>/
```

`results/` is generated runtime output and is ignored by `.gitignore`, so the smoke test should not dirty a normal git worktree.

Expected core artifacts are under `ai_company/`:

```text
results/ai_company_task_harness/<run-id>/ai_company/meeting_decision.json
results/ai_company_task_harness/<run-id>/ai_company/task_harness_report.json
results/ai_company_task_harness/<run-id>/ai_company/reviewer_verdicts.json
results/ai_company_task_harness/<run-id>/ai_company/artifact_verify_report.json
results/ai_company_task_harness/<run-id>/ai_company/subagent_claim_ledger.json
results/ai_company_task_harness/<run-id>/ai_company/watchdog_report.json
results/ai_company_task_harness/<run-id>/ai_company/main_agent_memory_guard_report.json
```

## What This Demo Proves

- The repo is no longer documentation-only.
- The strict subagent profile model is visible in artifacts.
- The mock flow creates meeting, task assignment, execution, reviewer, claim ledger, memory guard, and verification outputs.
- The final `task_harness_report.json` reports `overall_status=pass` for the bundled release-readiness demo.

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

## Dashboard Placeholder

This public smoke package includes dashboard helper scripts and an `agent_os_mvp` placeholder, but it does not include a runnable backend/frontend dashboard yet.

You may run the helpers only as source checks:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Do not expect `http://127.0.0.1:5174` to serve a web UI from this public smoke package. A runnable dashboard should be implemented as a separate follow-up.

## Live Router Mode

Live router mode is not bundled as a runnable script in this public smoke package. Do not run `scripts/run_common_research_with_router.sh`; that script is not part of the current public checkout.

For live mode, first validate your own company environment:

1. Confirm your local/open-source model service is running and points to the correct model location.
2. If your LLM models were moved to the Windows `D:` drive, update Ollama, LM Studio, or your model server configuration before testing this repo.
3. Validate the model endpoint, for example `/v1/models` or `/api/tags`.
4. Validate Claude Code Router `/health`.
5. Validate Claude Code Router `/v1/messages`; `/health` alone is not enough.

The mock demo does not depend on any model path, including models stored on `D:`.

## Repository Contents

- `.claude/skills/research-task-orchestrator/`: Claude Code skill entry and dashboard helper checks
- `scripts/verify_install.py`: first-run checkout validation
- `scripts/run_ai_company_task_harness.py`: standalone mock demo harness
- `docs/ai_specs/ai-company-release-readiness-strict-demo.json`: demo spec
- `tests/fixtures/ai_company_release_readiness_demo/`: bounded demo input evidence
- `agent_os_mvp/`: dashboard placeholder for future packaging

## Safety

This repository should not contain API keys, passwords, Docker images, model weights, result caches, `.venv`, `node_modules`, or runtime logs.
