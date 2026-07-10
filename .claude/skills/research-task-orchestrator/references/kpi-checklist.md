## KPI Checklist

Use these checks for a production-style research task:

- Meeting converges within bounded rounds.
- Every non-review task has an owner, scope, fallback, and acceptance criteria.
- Scope stays within the file-count limit.
- Every execution attempt emits a status file.
- Timeout, router, and overflow counts are visible in the final report.
- Reviewer can block false success.
- Post-verify returns structured JSON with `score` and `all_passed`.
- Goal stop hook can evaluate the latest run with one cheap command.

Strong target:
- `accepted_count >= 1`
- `replan_required_count = 0` for simple cases
- `failure_family_counts.timeout = 0`
- `failure_family_counts.router = 0`
- `failure_family_counts.overflow = 0`
- `artifact_score = 1.0`
