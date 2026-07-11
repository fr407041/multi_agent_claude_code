# Restore / Offline Bundle

This repository no longer depends on `release/bundle.part*.b64` for the normal first-run path.

The primary supported path is now:

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

If an offline bundle is published later, it is optional backup material only. A fresh clone of this repository should already contain the runnable mock demo, the `research-task-orchestrator` skill, the demo spec, the harness scripts, and the dashboard source.

## What Must Exist In A Runnable Checkout

```text
.claude/skills/research-task-orchestrator/
scripts/run_ai_company_task_harness.py
configs/ai_company/
docs/ai_specs/ai-company-release-readiness-strict-demo.json
tests/fixtures/ai_company_release_readiness_demo/
agent_os_mvp/
```

Run `scripts/verify_install.py` before attempting live router mode.
