# Multi-Agent Claude Code

Status:

- Runnable mock demo: yes
- Live router demo: requires Claude Code Router + open-source LLM
- Known live blocker: some CCR `/v1/messages` setups may timeout even when the model endpoint itself works

This repository provides a company Ubuntu reference for an AI-company style `Claude Code + Claude Code Router + open-source LLM` workflow. The first-run path is intentionally mock-only so a fresh clone can verify the orchestration artifacts without Docker, Ollama, Claude Router, API keys, or model services.

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

Expected core artifacts:

```text
meeting_decision.json
task_harness_report.json
reviewer_verdicts.json
artifact_verify_report.json
ai_company/subagent_claim_ledger.json
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

## Dashboard

The skill includes dashboard install/start helpers. In a real Ubuntu project:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Open:

```text
http://127.0.0.1:5174
```

## Live Router Mode

Live mode is separate from the mock smoke test:

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

Live mode requires your company environment to already have Claude Code, Claude Code Router, and an OpenAI-compatible local/open-source LLM endpoint. The current historical live result is documented in `docs/DEMO_RESULT.zh-TW.md`: CCR health/model endpoint can work while `/v1/messages` may still timeout, so live readiness must be verified in your own router setup.

## Repository Contents

- `.claude/skills/research-task-orchestrator/`: Claude Code skill entry and dashboard helpers
- `scripts/verify_install.py`: first-run checkout validation
- `scripts/run_ai_company_task_harness.py`: standalone mock demo harness
- `docs/ai_specs/ai-company-release-readiness-strict-demo.json`: demo spec
- `tests/fixtures/ai_company_release_readiness_demo/`: bounded demo input evidence
- `agent_os_mvp/`: dashboard source entrypoint placeholder for company packaging

## Safety

This repository should not contain API keys, passwords, Docker images, model weights, result caches, `.venv`, `node_modules`, or runtime logs.
