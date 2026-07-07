# Agent OS MVP Dashboard

This directory is the dashboard source entry for the AI-company run monitor.

For issue #1, the first-run mock demo does not require starting the dashboard. The dashboard helpers are included so company users can install/start the web monitor from the `research-task-orchestrator` skill path.

Expected full dashboard behavior:

- Read run artifacts from `results/ai_company_task_harness`.
- Show All runs summary.
- Show Current run agent board.
- Show trustworthiness, failures by agent, profile governance, claim/evidence, memory guard, and watchdog state.

Start URL after full setup:

```text
http://127.0.0.1:5174
```
