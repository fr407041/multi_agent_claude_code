---
name: research-task-orchestrator
description: Run bounded AI-company style tasks with strict subagent profiles, claim/evidence ledger, reviewer verification, memory guard, watchdog, and dashboard support.
---

# Research Task Orchestrator

Use this skill when the user asks Claude Code to run a complex task through an AI-company style workflow instead of a single large prompt.

## Default Protocol

1. Keep simple tasks simple.
2. For complex or high-risk tasks, use strict subagent profiles.
3. Split the task into bounded jobs.
4. Require every accepted subagent result to include:
   - short summary
   - key claims
   - evidence refs
   - confidence
   - limitations
   - handoff next action
5. Use reviewer verification before declaring success.
6. Use memory checkpoints instead of replaying full raw logs.
7. Use watchdog reports to detect missing status, timeout, missing reviewer, and false success.

## First Run Smoke Test

From the repository root:

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

## Dashboard

Install and start the bundled dashboard helpers:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Open `http://127.0.0.1:5174`.

## Live Router Mode

Live mode requires Claude Code Router and an OpenAI-compatible open-source LLM endpoint. Do not treat live mode as healthy until `/v1/messages` is validated, not just `/health` or `/v1/models`.
