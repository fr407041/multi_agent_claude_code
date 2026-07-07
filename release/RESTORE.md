# Restore / Offline Bundle

The normal first-run path no longer depends on `release/bundle.part*.b64`.

A fresh clone should already contain the runnable mock demo:

```bash
python3 scripts/verify_install.py
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

If an offline bundle is published later, it is optional backup material only. The primary supported checkout must include:

```text
.claude/skills/research-task-orchestrator/
scripts/run_ai_company_task_harness.py
scripts/verify_install.py
docs/ai_specs/ai-company-release-readiness-strict-demo.json
tests/fixtures/ai_company_release_readiness_demo/
agent_os_mvp/
```

Run `scripts/verify_install.py` before attempting live router mode.
