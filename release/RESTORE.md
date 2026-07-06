# Restore release bundle

This repository stores the implementation bundle as base64 chunks because the upload channel is text-only.

## Restore on Ubuntu

```bash
cat release/bundle.part*.b64 | base64 -d > multi_agent_claude_code_release.zip
unzip multi_agent_claude_code_release.zip -d multi_agent_claude_code_release
cd multi_agent_claude_code_release
```

Then install the Claude Code skill into your project:

```bash
mkdir -p .claude/skills
cp -R .claude/skills/research-task-orchestrator <your-project>/.claude/skills/
```

Run the demo:

```bash
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
# or live, if Claude Code Router is already working:
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

Start dashboard:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Open:

```text
http://127.0.0.1:5174
```
