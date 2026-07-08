# Agent OS MVP Dashboard

This folder contains a lightweight dashboard for AI-company run artifacts.

It is intentionally small:

- FastAPI backend
- SQLite metadata cache
- React + Vite frontend
- Linux helper scripts

## What It Reads

The dashboard does not run `claude` or `ccr`. It reads artifacts from:

```text
results/ai_company_task_harness/<run-id>/ai_company/
```

It shows:

- All runs summary
- Current run agent board
- trustworthiness and artifact checks
- failures by agent
- profile governance
- claim/evidence summary
- memory guard and watchdog state

## Quick Start

```bash
cd agent_os_mvp/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cd ../frontend
npm install
cd ..
bash ./start-dashboard.sh
```

Open:

```text
http://127.0.0.1:5174
```

Stop:

```bash
bash ./stop-dashboard.sh
```

## Skill Install Path

Company users normally use:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

The skill installer copies bundled dashboard assets into `./agent_os_mvp`, installs Python/npm dependencies, then starts the local services.

## Do Not Commit Runtime Outputs

Do not commit:

- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `logs/`
- `data/`
- `*.db`
- `*.sqlite`
