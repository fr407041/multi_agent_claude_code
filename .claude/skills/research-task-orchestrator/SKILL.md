---
name: research-task-orchestrator
description: Run bounded multi-agent research or analysis tasks through Claude Code plus router with a meeting phase, compact evidence preparation, small child-job dispatch, artifact verification, strict/custom subagent profiles, and an installable web dashboard. Use when Codex needs to turn an open-ended research request into a measurable workflow that avoids token overflow, detects false success, limits agent tools/skills/MCP scope, records meeting decisions/task assignment/KPI results, or installs/starts/uses the bundled ai-company dashboard.
---

# Research Task Orchestrator

Use this skill to run a reusable `ai-company` style workflow without sending broad context to a child worker.

The skill bundles the verified orchestration runtime and lightweight web dashboard. Installing the skill alone is enough to create a project-local runtime on first use.

## Workflow

1. Define a bounded task.
2. Prepare compact evidence files before dispatch.
3. Run a short meeting to converge on owners, scope, and fallback.
4. Dispatch only small jobs to child workers.
5. Verify the final artifact with explicit checks and score thresholds.

## Required behavior

- Never ask a child worker to read an entire repo or large folder.
- Keep each non-review task at 3 files or fewer.
- Prefer deterministic prep scripts over live browsing inside child workers.
- Require a post-verify step with `all_passed=true` before considering the run successful.
- Require every accepted subagent result to have at least one key claim with traceable evidence refs.
- Record meeting output in structured JSON so stop hooks and KPI checks can validate it.
- For live meetings, default to `AI_COMPANY_LIVE_MEETING_TRANSPORT=auto`. This invokes the installed Claude CLI and inherits its existing Router configuration without selecting a model endpoint.
- For live action execution, prefer `AI_COMPANY_LIVE_TASK_URL`. It inherits the existing Claude/Router model configuration and must not require the skill to know the model name or model-service URL.
- For action tasks with more than three expected artifacts, use staged execution. Keep each stage at two artifacts by default, preserve transport raw output, and treat process success without a complete action manifest as contract failure.
- Mark KPI and report outputs as derived artifacts when their values must come from executed code. Do not accept direct model-written static metrics as verified results.
- Keep `/run-task` for full worker tasks. Never treat it as a synchronous single meeting-turn API.
- Accept live meeting success only when all required roles completed provider turns and `live_degraded=false`; deterministic fallback is protection, not live success.
- Preserve each live turn's raw artifact, transport, duration, retry attempt, and token usage. Retry transient transport failures at most twice and malformed output at most once.
- Keep subagent profiles simple by default. Only enable strict profile controls when requested or triggered by risk.
- When a user asks for restricted agents, custom subagents, strict subagents, profiled agents, role-specific skills/MCP/tool policy, or reducing one agent's tool burden, treat it as a request to use Custom Subagent Profiles.
- If the user asks to run with strict/custom/restricted agents, create or update a task spec with `agent_profile_mode = strict` unless the user explicitly requests simple mode.
- In strict profile mode, preserve role profile, allowed skills, MCP group, tool policy, settings overlay, and policy level in run artifacts.
- Enable the main-agent memory guard for token-overflow-prone, long-running, large research, or multi-round analysis tasks.
- Enable the file context guard before any worker reads task files. Inspect file size first, load only bounded chunks, and record included/skipped bytes in the worker status.
- When a user asks for the web UI, dashboard, monitor, or latest run view, install the bundled dashboard if it is not already present.
- When a user asks to monitor a run, check whether work is stuck, or recover a silent failure, run the main-agent watchdog instead of relying on dashboard display alone.
- Do not install Claude Code, Claude Code Router, or model services. This skill assumes those already exist in the company Ubuntu environment.
- Run strict package and spec preflight before every task. Do not start a meeting or worker when package integrity, worker template, verifier, or artifact contract validation fails.
- Never repair a mixed-version package by adding one missing file manually. Install one atomic release and verify its manifest.

## Skill-only runtime

Install or activate the bundled release:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_runtime.sh
```

Run the bundled strict demo or a project spec:

```bash
bash .claude/skills/research-task-orchestrator/scripts/run_task.sh \
  docs/ai_specs/ai-company-release-readiness-strict-demo.json --mode live
```

The installer writes only to `.ai-company/runtime/releases/<release_id>` in the project and atomically updates `.ai-company/runtime/current`. It must not modify global Claude, Router, model, npm, or shell configuration.

## Repo entrypoints

- Generic spec example:
  - `docs/ai_specs/common-research-summary-example.json`
- Generic prep script:
  - `scripts/prepare_common_research_case.py`
- Generic artifact verifier:
  - `scripts/verify_common_research_artifact.py`
- Generic runner:
  - `scripts/run_common_research_with_router.sh`
- Live meeting smoke:
  - `scripts/smoke_live_meeting.py`
- Generic goal check:
  - `scripts/check_common_research_goal.js`
- Simple Web GUI docs:
  - `docs/common_research_orchestrator_zh-TW.md`
- Bundled Simple Web GUI payload:
  - `assets/agent_os_mvp/`
- Dashboard scripts:
  - `scripts/install_dashboard.sh`
  - `scripts/start_dashboard.sh`
  - `scripts/stop_dashboard.sh`
  - `scripts/smoke_dashboard.sh`
- Main-agent memory guard:
  - `scripts/main_agent_memory_guard.py`
- Subagent claim ledger:
  - `scripts/subagent_claim_ledger.py`
- Main-agent watchdog:
  - `scripts/run_ai_company_watchdog.py`
- Agent profile registry:
  - `configs/ai_company/agent_profiles.json`
  - `agents/roles/*.md`
  - `agents/settings/*.json`
- Custom subagent profile guide:
  - `references/custom-subagent-profiles.md`

## Claude Code invocation patterns

This skill must support these natural-language calls:

- "Use the research-task-orchestrator skill to run this task with strict subagents: <task>"
- "Use the research-task-orchestrator skill to create a strict-agent spec for this task: <task>"
- "Use the research-task-orchestrator skill to run this task with research_agent, synthesis_agent, and reviewer_worker, then open the dashboard: <task>"
- "Use the research-task-orchestrator skill to inspect profile violations in the latest run."
- "Use the research-task-orchestrator skill to run in simple mode: <task>"

When invoked this way, do not ask the user to manually edit JSON unless required. Generate or update the spec, run the bounded workflow, and report the run path, dashboard URL, selected profile mode, KPIs, and any violations.

## How to use the generic pattern

1. Copy `tests/fixtures/ai_company_common_research_case` to a new case directory.
2. Replace `research_brief.md`, `evidence_summary.txt`, and `artifact_requirements.json` with the new task inputs.
3. Duplicate `docs/ai_specs/common-research-summary-example.json` and point `scope_subdir` plus `scope_copy_from` at the new case directory.
4. Keep the worker template as `summary_markdown` unless a broader file-edit task is really needed.
5. Run the spec through the generic runner.

## Web Dashboard

Use the bundled single-page Web GUI when you need a low-dependency monitor for:
- current run status
- active agents
- final result credibility
- explicit main-agent chat sessions and framework lifecycle events
- expandable technical details only when needed

Design rules:
- Keep the default view to one page.
- Show only three primary sections first: current status, who is working, and whether the result is trustworthy.
- Keep meeting traces, prompts, raw output, and logs behind expandable details.
- Use the existing FastAPI + React stack. SQLite is the direct-Ubuntu default; PostgreSQL is optional for Docker Compose deployments.
- Record explicit messages, status, tool/action metadata, and artifact references only. Never request, store, or display hidden chain-of-thought.

Dashboard installation flow:

1. Locate the project root. Prefer `AI_COMPANY_PROJECT_ROOT` when set; otherwise use the current git root; otherwise use the current working directory.
2. If `./agent_os_mvp/backend/.venv` or `./agent_os_mvp/frontend/node_modules` is missing, run:

```bash
bash .claude/skills/research-task-orchestrator/scripts/install_dashboard.sh
```

3. Start the dashboard with:

```bash
bash .claude/skills/research-task-orchestrator/scripts/start_dashboard.sh
```

4. Show the user:
   - Backend health: `http://127.0.0.1:8010/health`
   - Frontend URL: `http://127.0.0.1:5174`
   - Artifacts root: `results/ai_company_task_harness`

If the dashboard opens with zero runs, explain that no compatible run artifacts have been generated yet. Do not describe that as a dashboard failure.

Stop the dashboard with:

```bash
bash .claude/skills/research-task-orchestrator/scripts/stop_dashboard.sh
```

Run a minimal dashboard check with:

```bash
bash .claude/skills/research-task-orchestrator/scripts/smoke_dashboard.sh
```

## Stop and replan conditions

- Child output mentions context-window or output-token errors.
- Router returns empty, partial, or transport-failure responses.
- A subagent summary contains a key claim without an evidence ref.
- A strict subagent violates its assigned agent profile policy.
- Reviewer marks `REPLAN_REQUIRED`, `REPAIR_REQUIRED`, or `FALSE_SUCCESS_BLOCKED`.
- Artifact verification score is below the required threshold.

When any of these happens, reduce scope first. Do not retry the same broad job unchanged.

## Main Agent Watchdog

Use the watchdog to detect silent run failures that are not obvious from a chat response.

Run one check:

```bash
python3 scripts/run_ai_company_watchdog.py <run_dir> --once
```

Monitor the latest run continuously:

```bash
python3 scripts/run_ai_company_watchdog.py --watch
```

The watchdog writes:
- `ai_company/watchdog_report.json`
- `ai_company/watchdog_events.jsonl`
- `ai_company/heartbeat.json`

Watchdog responsibilities:
- detect missing status files
- mark stale non-terminal tasks as `CHILD_TIMEOUT`
- regenerate reviewer verdicts when missing or incomplete
- rebuild claim ledger and verify accepted claims still have evidence
- rerun final artifact verification when possible
- escalate with `WATCHDOG_ESCALATION_REQUIRED` when bounded recovery cannot make the run trustworthy

The watchdog must stay bounded. It must not loop forever, install tools, change router/model settings, or replay large raw logs into main context.

## Main Agent Memory Guard

## Goal-Driven DAG

When the user supplies only a natural-language goal and asks for automatic planning, run:

```bash
python3 scripts/run_goal_driven_workflow.py --goal "<goal>" --mode live
```

Goal-driven live planning is bounded by `--max-replans`. Preserve every planner attempt and reject invalid network, worker/output, profile, or DAG contracts before workers start. Use `managed_single_file` for one output and `managed_multi_file` for two outputs. A verified partial delivery remains `partial` / `NEEDS REPAIR`; never promote it to pass.

Use `--scope-copy-from` and repeated `--supplied-input` for bounded supplied evidence. The planner must produce only the generic acquire/analyze/generate/execute/verify/synthesize capabilities. Do not bypass `dag_validation_report.json`, and do not treat process exit zero as acceptance. Final status comes only from `ai_company/final_run_verdict.json`.

Common workflows use `generic_contract`. Fixture verifiers require explicit spec selection and must never be inferred from a filename or `summary.md`.

Use the phase-gate memory guard to prevent the master process from carrying too much accumulated context.

The guard runs after:
- `after_materialize`
- `after_prep`
- `after_meeting`
- `after_execution`
- `after_post_verify`

Each gate estimates portable context pressure from run artifacts with `estimated_tokens = ceil(chars / 4)`. If the estimate exceeds `main_agent_memory_token_threshold`, write:
- `ai_company/main_agent_memory_checkpoint.md`
- `ai_company/main_agent_memory_checkpoint.json`
- `ai_company/main_agent_memory_guard_report.json`

The checkpoint must include:
- goal
- current phase
- decisions
- active tasks
- completed tasks
- failures
- open risks
- next recommended action
- files/artifacts map

When a checkpoint exists:
- Use it as condensed prior state for meeting and reassignment decisions.
- Do not replay full prior logs into prompts.
- Do not pass the checkpoint to child workers as broad context unless a specific bounded task requires one short excerpt.

Default thresholds live in `configs/ai_company/task_harness.defaults.json`.

## File Context Guard

Use the file context guard to prevent child workers from loading large files or folders into one prompt.

Before every live worker call:
- Inspect each referenced file with filesystem metadata before reading content.
- Apply role-specific input/output token budgets from `configs/ai_company/task_harness.defaults.json`.
- If a file is above `file_soft_limit_bytes`, pass bounded chunks plus a context manifest instead of the full file.
- If a file is above `file_hard_limit_bytes`, stop before calling the model and write `OVERFLOW_DETECTED`.
- If total estimated input exceeds `context_hard_tokens_per_turn`, stop before calling the model and write `OVERFLOW_DETECTED`.
- Record `context_guard.manifest`, `context_guard.skipped_bytes`, `context_guard.estimated_input_tokens`, and `context_guard.blocked_before_model` in the worker status.

The model must be told that omitted bytes were not read. A worker must not infer from skipped content.

When context guard blocks a call:
- Do not spend live model tokens on the oversized request.
- Replan into a smaller job, a chunk summary job, or an explicit evidence extraction task.
- Preserve the same agent profile and claim/evidence requirements on reassignment.

## Subagent Summary Evidence Contract

Keep subagent handoff short, but never make the main agent rely on summary text alone.

Every subagent result must be represented in `ai_company/subagent_claim_ledger.json` with:
- `summary`: 3-5 sentence bounded handoff
- `key_claims`: one or more concrete claims
- `evidence_refs`: raw/status/file/source references for each claim
- `confidence`: `high`, `medium`, or `low`
- `limitations`: missing evidence, uncertainty, or known weak points
- `handoff_next`: the suggested next main-agent action

Reviewer rules:
- An accepted subagent result must have at least one key claim.
- Every key claim must have at least one evidence ref.
- A success response with missing evidence must be blocked as `FALSE_SUCCESS_BLOCKED` or repaired before acceptance.
- If a confident summary is stronger than the available evidence, mark an uncertainty gap and request repair.

Main-agent rules:
- Read the claim ledger first when replanning.
- Use raw output only for traceability, not as default main context.
- When a memory checkpoint exists, preserve the claim ledger inside the checkpoint so later phases can continue from claims plus evidence refs instead of full logs.

## Custom Subagent Profiles

Use dual-layer profiles:
- Simple mode is the default and keeps existing one-command usage unchanged.
- Strict mode is enabled by `agent_profile_mode = strict`, explicit task `agent_profile`, or risk-triggered `auto` promotion.

The profile registry lives at `configs/ai_company/agent_profiles.json`. Each profile defines:
- allowed skills
- allowed MCP groups
- tool policy
- settings overlay
- role prompt
- scope limits
- recovery policy

For Claude Code 2.1.42 compatibility, profile isolation is implemented through wrapper-time prompt/settings shaping plus reviewer/watchdog verification. Do not claim hard sandboxing unless the runtime provides it.

Profile policy levels:
- `advisory + verified-posthoc` for simple mode
- `enforced-by-wrapper + verified-posthoc` for strict mode

Every profiled child result should preserve:
- `agent_profile`
- `profile_mode`
- `effective_policy_level`
- `effective_allowed_skills`
- `effective_allowed_mcp_groups`
- `effective_tool_policy`

Reviewer and watchdog must block or escalate strict profile violations as `PROFILE_POLICY_VIOLATION`.

When a user asks for strict/custom/restricted agents, read `references/custom-subagent-profiles.md` and follow its spec, role-selection, run, and reporting contract.

Strict/custom subagent skill flow:

1. Translate the user's task into a task spec. Add top-level `agent_profile_mode: "strict"` unless the user requested `simple`.
2. Assign role profiles deterministically:
   - planning/scoping -> `planner_agent`
   - evidence/source extraction -> `research_agent`
   - final writing/synthesis -> `synthesis_agent`
   - artifact review -> `reviewer_worker`
   - overflow/router/failure risk -> `risk_reviewer`
   - final accept/replan/escalate -> `decision_agent`
   - code implementation -> `executor_backend`
   - documentation implementation -> `executor_docs`
3. Preserve `agent_profile`, `profile_mode`, `effective_policy_level`, allowed skills, allowed MCP groups, and tool policy in every job artifact.
4. Run reviewer, watchdog, memory guard, and claim ledger checks with profile awareness.
5. Treat `PROFILE_POLICY_VIOLATION`, missing evidence refs, or strict-mode downgrade as visible failures unless the run explicitly records an accepted fallback.
6. Report profile KPIs:
   - `profile_attachment_rate`
   - `profile_policy_visibility_rate`
   - `profile_policy_violation_count`
   - `false_success_block_rate`
   - `downgrade_logging_rate`

Output contract for strict/custom subagent runs:

- Generated or used spec path.
- Run directory.
- Dashboard URL if dashboard is installed/running.
- Run mode: `simple`, `strict`, or `auto-resolved strict`.
- Agent/profile mapping.
- Profile KPI summary.
- Any policy violations, downgrades, or reviewer-blocked false success.

## Company Ubuntu Install Model

For company rollout, publish the whole `.claude/skills/research-task-orchestrator` folder. Users should not need a separate dashboard zip.

The first dashboard install may run `python3 -m venv`, `pip install -r requirements.txt`, and `npm install`. These commands are limited to the local `agent_os_mvp` dashboard directory.

## References

- Read `references/spec-pattern.md` when creating a new generic research spec.
- Read `references/kpi-checklist.md` when deciding what success criteria and score thresholds to enforce.
- Read `references/custom-subagent-profiles.md` when the user asks for strict/custom/restricted subagents or role-specific skill/MCP/tool control.

## Validation

After creating or changing this skill, run:

```bash
python C:/Users/fr407/.codex/skills/.system/skill-creator/scripts/quick_validate.py .claude/skills/research-task-orchestrator
```
