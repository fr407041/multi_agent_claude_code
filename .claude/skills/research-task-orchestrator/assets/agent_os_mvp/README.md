# Agent OS MVP Dashboard

This folder contains a lightweight dashboard for AI-company run artifacts.

It supports a simple local mode and an optional session-service mode:

- FastAPI backend
- SQLite metadata cache by default; PostgreSQL in Docker Compose
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

The optional Main Agent Chat invokes the installed Claude CLI and inherits its existing Router configuration. It records only explicit messages, lifecycle events, and artifact references, never hidden reasoning.

## Session And Chat API

```text
POST /api/v1/chat
GET  /api/v1/dashboard/{session_id}
GET  /api/v1/dashboard/config
```

Chat requests use `{"prompt":"Run a bounded task","session_id":null}`. Reuse the returned `session_id` on later turns. Session artifacts are written below `AGENT_OS_SESSION_ROOT`.

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

`start-dashboard.sh` builds the React production bundle and serves it with Vite preview. It does not expose the Vite development runtime.

The backend reads `results/ai_company_task_harness` by default. Point it at an externally mounted run root when needed:

```bash
AI_COMPANY_RESULTS_ROOT=/runs/company_tasks bash ./start-dashboard.sh
```

For strict browser-level smoke validation with Chrome or Chromium:

```bash
AGENT_OS_REQUIRE_BROWSER_SMOKE=1 bash ./smoke-dashboard.sh
```

Stop:

```bash
bash ./stop-dashboard.sh
```

## Frontend Toolchain

The frontend is pinned to a reproducible Vite 7 toolchain:

- `vite: 7.3.6`
- `@vitejs/plugin-react: 5.2.0`

Clean `npm ci` and `npm run build` are expected to pass on Node 20 and Node 22.

## Skill Install Path

Company users normally use:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

The skill installer copies bundled dashboard assets into `./agent_os_mvp`, installs Python/npm dependencies, then starts the local services.

## Docker Compose With PostgreSQL

```bash
export CLAUDE_CONFIG_DIR="$HOME/.claude"
export AGENT_OS_POSTGRES_PASSWORD="change-this-local-password"
docker compose up --build
```

Docker mode installs Claude Code in the backend image and mounts the existing Claude configuration read-only. Direct Ubuntu installation continues to use SQLite and does not require Docker or PostgreSQL.

## Do Not Commit Runtime Outputs

Do not commit:

- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `logs/`
- `data/`
- `*.db`
- `*.sqlite`
