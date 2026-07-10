# Custom Subagent Profiles

Use this reference when a user asks for restricted agents, custom subagents, strict subagents, profiled agents, role-specific skill/MCP/tool limits, or reducing one agent's tool/skill attention burden.

## Goal

Keep simple tasks one-command simple, while letting complex tasks run through explicit, constrained, inspectable role profiles.

The system is Claude Code 2.1.42 compatible:

- It does not assume hard native subagent sandboxing.
- It uses wrapper-time prompt/settings shaping where available.
- It verifies profile policy after execution through reviewer, watchdog, claim ledger, and dashboard artifacts.
- It must not silently claim strict isolation if only advisory enforcement was possible.

## User-facing commands

Use these exact patterns as supported skill calls:

```text
Use the research-task-orchestrator skill to run this task with strict subagents: <task>
```

```text
Use the research-task-orchestrator skill to create a strict-agent spec for this task: <task>
```

```text
Use the research-task-orchestrator skill to run this task with research_agent, synthesis_agent, and reviewer_worker, then open the dashboard: <task>
```

```text
Use the research-task-orchestrator skill to inspect profile violations in the latest run.
```

```text
Use the research-task-orchestrator skill to run in simple mode: <task>
```

## Spec knobs

Top-level:

```json
{
  "agent_profile_mode": "auto"
}
```

Allowed values:

- `auto`: default. Start simple, promote to strict for risk triggers.
- `simple`: backward-compatible behavior.
- `strict`: attach role profiles and verify policy.

Task-level:

```json
{
  "owner_role": "research_agent",
  "agent_profile": "research_agent"
}
```

If `agent_profile` is omitted, derive it from `owner_role`.

## Role selection

Use these defaults:

- `meeting_coordinator`: meeting convergence and bounded agenda.
- `planner_agent`: scope, dependencies, task decomposition.
- `research_agent`: evidence extraction, source traceability, claim support.
- `synthesis_agent`: final artifact drafting from evidence bundles.
- `reviewer_worker`: contract review, evidence coverage, false-success blocking.
- `risk_reviewer`: overflow, router, timeout, loop, and recovery analysis.
- `decision_agent`: accept, replan, escalate decisions.
- `executor_backend`: code implementation tasks.
- `executor_docs`: documentation tasks.

## Strict run procedure

1. Generate or update a spec with `agent_profile_mode: "strict"`.
2. Split the task into bounded assignments.
3. Attach `agent_profile` to each assignment.
4. Execute through the existing task harness, not a second orchestrator.
5. Require each accepted subagent result to include claim/evidence metadata.
6. Run reviewer and watchdog with profile awareness.
7. Open or report dashboard state.

## Required artifacts

Each strict run should make these visible when applicable:

- `meeting_decision.json`
- `task_harness_report.json`
- `reviewer_verdicts.json`
- `subagent_claim_ledger.json`
- `artifact_verify_report.json`
- `ai_company/main_agent_memory_guard_report.json`
- `ai_company/watchdog_report.json`

Job artifacts should include:

- `agent_profile`
- `profile_mode`
- `effective_policy_level`
- `effective_allowed_skills`
- `effective_allowed_mcp_groups`
- `effective_tool_policy`

## KPI gate

For seeded strict-profile tests, target:

- `profile_attachment_rate = 1.0`
- `profile_policy_visibility_rate = 1.0`
- `profile_violation_detection_rate = 1.0`
- `false_success_block_rate = 1.0`
- `downgrade_logging_rate = 1.0`
- `backward_compat_pass_rate = 1.0`

If a run violates a strict role contract, do not report success just because the output looks useful. Mark it with `PROFILE_POLICY_VIOLATION`, `FALSE_SUCCESS_BLOCKED`, `REPLAN_REQUIRED`, or another explicit failure family.

## Dashboard checks

In the dashboard, verify:

- Current run mode is `strict` or `auto-resolved strict`.
- Agent board shows role, profile, state, task id, and policy level.
- Agent profile governance shows violations or downgrades.
- Failures by agent attributes profile failures to the owning agent.
- Claim/evidence view shows key claims and refs for accepted work.

## Minimal strict example

```json
{
  "id": "strict-research-example",
  "title": "Strict evidence-based research example",
  "agent_profile_mode": "strict",
  "tasks": [
    {
      "id": "research-001",
      "owner_role": "research_agent",
      "agent_profile": "research_agent",
      "objective": "Collect evidence and produce key claims with evidence refs."
    },
    {
      "id": "synthesis-001",
      "owner_role": "synthesis_agent",
      "agent_profile": "synthesis_agent",
      "objective": "Draft a concise summary using only accepted evidence."
    },
    {
      "id": "review-001",
      "owner_role": "reviewer_worker",
      "agent_profile": "reviewer_worker",
      "objective": "Verify claim coverage, evidence refs, and profile policy."
    }
  ]
}
```

## Reporting contract

After running, report:

- Spec path.
- Run directory.
- Dashboard URL.
- Agent/profile mapping.
- Overall status.
- Profile KPI summary.
- Violations, downgrades, or reviewer-blocked false success.
