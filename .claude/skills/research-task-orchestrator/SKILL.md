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

Mock mode does not require Docker, Claude Code Router, Ollama, API keys, or a local model.

## Dashboard Placeholder

This public smoke package includes dashboard helper checks, not a complete runnable web dashboard.

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

These commands verify dashboard placeholder/source presence. Do not treat `http://127.0.0.1:5174` as available until a real backend/frontend dashboard is added.

## Live Router Mode

Live mode is not bundled in this public smoke package. It requires a real Claude Code Router setup and an OpenAI-compatible open-source LLM endpoint.

If models were moved to the Windows `D:` drive, update Ollama, LM Studio, or your model server first. The skill only talks to the model service endpoint; it does not load model files directly.

Do not treat live mode as healthy until all of these pass:

1. model endpoint `/v1/models` or `/api/tags`
2. Claude Code Router `/health`
3. Claude Code Router `/v1/messages`
