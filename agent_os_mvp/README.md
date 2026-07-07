# Agent OS MVP Dashboard Placeholder

This directory is a placeholder for a future AI-company run monitor dashboard.

The current public smoke package does not include a runnable backend/frontend dashboard. It exists so the skill and docs have a stable place to describe the intended dashboard package boundary.

## Current State

- Mock demo does not require dashboard startup.
- `install_dashboard.sh` and `start_dashboard.sh` are source/placeholder checks only.
- There is currently no bundled `frontend/package.json` or `backend/app/main.py` in this public checkout.
- Do not expect `http://127.0.0.1:5174` to serve a web UI yet.

## Intended Future Behavior

A future runnable dashboard should:

- Read run artifacts from `results/ai_company_task_harness`.
- Show All runs summary.
- Show Current run agent board.
- Show trustworthiness, failures by agent, profile governance, claim/evidence, memory guard, and watchdog state.

Track this as a separate dashboard implementation task, not as part of the mock-first smoke package.
