# Multi-Agent Claude Code

Status:

- Runnable mock demo: yes
- Mock demo dependencies: no Docker, no Claude Code Router, no Ollama, no API key, no model service
- Live router demo: available through `scripts/run_common_research_with_router.sh`
- Dashboard web app: runnable FastAPI + React package under `agent_os_mvp`

This repository provides a company Ubuntu reference for an AI-company style `Claude Code + Claude Code Router + open-source LLM` workflow. The default path is mock-first so a fresh clone can verify orchestration artifacts before users configure live router, local model services, or dashboard runtime.

## First Run

Run these commands after cloning:

```bash
python3 scripts/verify_install.py --strict --json
python3 scripts/validate_ai_company_spec.py docs/ai_specs/ai-company-release-readiness-strict-demo.json
python3 scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

If your system uses `python` instead of `python3`:

```bash
python scripts/verify_install.py --strict --json
python scripts/validate_ai_company_spec.py docs/ai_specs/ai-company-release-readiness-strict-demo.json
python scripts/run_ai_company_task_harness.py docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode mock
```

Strict verification checks that the runner, workers, prep script, verifier, canonical spec, fixtures, profiles, skill, and dashboard all belong to one release. Do not start a live run when it reports `PACKAGE_INTEGRITY_FAILED` or `SPEC_CONTRACT_INVALID`.

For a skill-only company installation:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_runtime.sh
bash .claude/skills/research-task-orchestrator/scripts/run_task.sh \
  docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode live
```

A successful run writes:

```text
results/ai_company_task_harness/<run-id>/
```

Expected core artifacts are under `ai_company/`:

```text
meeting_decision.json
task_harness_report.json
reviewer_verdicts.json
artifact_verify_report.json
subagent_claim_ledger.json
watchdog_report.json
main_agent_memory_guard_report.json
```

## Live Router Mode

Live mode requires an existing Claude Code + Claude Code Router setup. The repository does not need to know which model service or model the router uses.

To verify that the agent meeting itself is live, run:

```bash
AI_COMPANY_LIVE_MEETING_TRANSPORT=auto \
python3 scripts/smoke_live_meeting.py
```

The smoke report must show:

```text
ok: true
meeting_mode: live
live_meeting_used: true
live_turn_count: 4 or higher
live_transport: claude_cli
live_degraded: false
```

For full live execution, run:

```bash
bash scripts/run_common_research_with_router.sh docs/ai_specs/ai-company-release-readiness-strict-demo.json live
```

Meeting turns and full worker tasks use separate boundaries:

1. Live meeting defaults to `claude --bare -p`, which inherits the existing router configuration.
2. `/run-task` remains a full worker-task API and is not used as a meeting-turn API.
3. Direct CCR `/v1/messages` is optional and only used with `AI_COMPANY_LIVE_MEETING_TRANSPORT=ccr_http`.
4. Model endpoint probes remain optional diagnostics when `AI_COMPANY_PROBE_MODEL_ENDPOINTS=1`.

Useful environment overrides:

```bash
export AI_COMPANY_LIVE_MEETING_TRANSPORT=auto
export AI_COMPANY_CLAUDE_BIN=claude
export AI_COMPANY_LIVE_MEETING_TIMEOUT_SEC=90
export AI_COMPANY_LIVE_TRANSPORT_RETRIES=2
export AI_COMPANY_LIVE_CONTRACT_RETRIES=1
```

Only environments that intentionally use direct CCR HTTP need these optional settings:

```bash
export AI_COMPANY_LIVE_MEETING_TRANSPORT=ccr_http
export CCR_MESSAGES_URL=http://127.0.0.1:3456/v1/messages
export CCR_API_KEY='<router-api-key>'
export CLAUDE_MODEL_ALIAS='<router-model-alias>'
```

The live runner performs a side-effect smoke check before the full live harness. The check is intentionally strict: the model must create the expected file content, not merely reply that it created it. If a qwen2.5 run returns a plausible success message without the verified file side effect, the run is blocked as `FAILED` instead of being counted as a successful AI-company execution.

### Deterministic Local-Model Action Executor

Some local models can route through CCR but still emit pseudo tool calls as text instead of producing real side effects. For side-effect-heavy live tasks, use the deterministic action executor:

```bash
export AI_COMPANY_LIVE_TASK_URL='http://127.0.0.1:8080/run-task'
python3 scripts/run_local_model_action_executor.py --task "Create a small probe file and verify it."
```

`AI_COMPANY_LIVE_TASK_URL` is the preferred provider-neutral transport. The executor does not need a model name or model-service URL; the existing Claude/Router runtime chooses them. Task wrappers may prepend verification logs, so the adapter extracts only the final JSON object containing an `actions` array. If the task API is not configured, the legacy CCR `/v1/messages` transport remains available with its existing model alias and authentication settings.

Large artifact contracts automatically use bounded staged execution. Override with `--execution-strategy single|staged`; use `--derived-artifact FILE` for KPI/report files that must be generated by an executed command rather than static model text. Truncated task responses are preserved and classified as `manifest_truncated` instead of being retried as an unspecified failure.

For business or domain acceptance, add runner-owned gates instead of trusting model-written KPI fields: `--result-status FILE:PATH`, `--compare-json ACTUAL:EXPECTED`, `--require-json-path FILE:PATH`, and restricted `--invariant 'FILE:PATH>0'`. These checks produce `ai_company/domain_verdict.json`; a failed domain verdict always fails the run even when files and provenance are valid.

The model must return strict JSON actions. The runner validates and executes only allowlisted actions:

```text
write_file
read_file
run_command
finish
```

All paths are restricted to the run worktree, commands are allowlisted, and dashboard-compatible artifacts are written under `results/ai_company_task_harness/<run-id>/`. This keeps task content generated by the open-source model while making file writes and command execution deterministic and auditable.

See [docs/LOCAL_MODEL_ACTION_EXECUTOR.zh-TW.md](docs/LOCAL_MODEL_ACTION_EXECUTOR.zh-TW.md) for the manifest contract and safety rules.

Semantic validators are schema-aware during bounded repair. When `--seed-file` points to JSON input, the runner infers available input fields and includes those field hints in repair feedback. This is important for local models such as `qwen2.5-coder:14b`: if a broad prompt uses the wrong field name, for example `duration` instead of `duration_sec`, the runner blocks false success and the next repair attempt receives the actual seeded schema context.

Company/offline acceptance should use a local fixture instead of public websites:

```bash
python3 scripts/run_local_model_action_executor.py \
  --task "Read data/input_records.json, write analyzer.py, run it, and produce output_summary.json with total_count, passed_count, failed_count, warning_count, and average_duration_sec." \
  --seed-file tests/fixtures/local_action_executor_offline_case/input_records.json=data/input_records.json \
  --expected-artifact analyzer.py \
  --expected-artifact output_summary.json \
  --expect-json-value output_summary.json:total_count=4 \
  --expect-json-value output_summary.json:passed_count=2 \
  --expect-json-value output_summary.json:failed_count=1 \
  --expect-json-value output_summary.json:warning_count=1 \
  --expect-json-value output_summary.json:average_duration_sec=21.25
```

Detailed D-drive and local model service setup is documented in [docs/LIVE_MODEL_SERVICE_SETUP.zh-TW.md](docs/LIVE_MODEL_SERVICE_SETUP.zh-TW.md). Moving models to Windows `D:` affects only the model service configuration, not mock mode.

## Dashboard

Install and start the bundled dashboard:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

Run the dashboard smoke check:

```bash
bash agent_os_mvp/smoke-dashboard.sh
```

Open:

```text
http://127.0.0.1:5174
```

The dashboard builds and serves a localhost production frontend bundle. If `8010` or `5174` is occupied by another service, stop that service or override ports:

```bash
AGENT_OS_BACKEND_PORT=8011 AGENT_OS_FRONTEND_PORT=5175 bash agent_os_mvp/smoke-dashboard.sh
```

To inspect an externally mounted run root, set `AI_COMPANY_RESULTS_ROOT` before starting or smoking the dashboard. Set `AGENT_OS_REQUIRE_BROWSER_SMOKE=1` to require a rendered Chrome/Chromium DOM check in addition to HTTP checks.

Stop:

```bash
bash .claude/skills/research-task-orchestrator/scripts/stop_dashboard.sh
```

The dashboard reads run artifacts from `results/ai_company_task_harness` and shows All runs, Current run agent board, trustworthiness, failures by agent, profile governance, claim/evidence, memory guard, and watchdog state.

## Claude Code Skill

Install the skill into a company Ubuntu project:

```bash
mkdir -p .claude/skills
cp -R .claude/skills/research-task-orchestrator .claude/skills/
```

Then ask Claude Code:

```text
Use the research-task-orchestrator skill to run this task with strict subagents: <your task>
```

## Repository Contents

- `.claude/skills/research-task-orchestrator/`: Claude Code skill entry, role profiles, dashboard installer, dashboard assets
- `scripts/verify_install.py`: first-run checkout validation
- `scripts/run_ai_company_task_harness.py`: mock/live harness entrypoint
- `scripts/run_common_research_with_router.sh`: live router preflight + harness runner
- `scripts/run_local_model_action_executor.py`: deterministic action bridge for local-model live side effects
- `scripts/smoke_live_tool_side_effect.py`: live side-effect guard for local open-source models
- `scripts/worker_claude_router*.sh`: repo-local live worker wrappers
- `docs/ai_specs/ai-company-release-readiness-strict-demo.json`: demo spec
- `docs/ai_specs/local-action-executor-offline-demo.json`: offline action-executor acceptance spec
- `tests/fixtures/ai_company_release_readiness_demo/`: bounded demo input evidence
- `tests/fixtures/local_action_executor_offline_case/`: no-network action-executor fixture
- `agent_os_mvp/`: runnable dashboard package

## Safety

This repository should not contain API keys, passwords, Docker images, model weights, result caches, `.venv`, `node_modules`, SQLite runtime DBs, or logs.
