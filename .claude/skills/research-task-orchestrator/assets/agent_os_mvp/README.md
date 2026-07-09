# Agent OS MVP Dashboard

Bundled dashboard assets for the research-task-orchestrator skill.

## Frontend Toolchain

The bundled frontend is pinned to a reproducible Vite 7 toolchain:

- `vite: 7.3.6`
- `@vitejs/plugin-react: 5.2.0`

Clean `npm ci` and `npm run build` are expected to pass on Node 20 and Node 22.

## Install

Install from project root:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Open `http://127.0.0.1:5174`.
