## Generic Research Spec Pattern

Use one spec per task family.

Required fields:
- `id`
- `goal`
- `strategy`
- `scope_subdir`
- `scope_copy_from`
- `prep_command`
- `post_verify_command`
- `jobs`
- `expectations`

Recommended defaults:
- one synthesis job before reviewer expansion
- `worker_template = "summary_markdown"` for summary-only tasks
- `require_change = true`
- `max_execution_jobs_run <= 2`
- `require_bounded_scope_rate = 1.0`
- `require_status_file_coverage_rate = 1.0`
- `require_post_verify_all_passed = true`

Keep the child instruction narrow:
- define exact output format
- define max length
- define required evidence points
- define explicit uncertainty language when the task is inferential

Prefer prep-time evidence packaging:
- `research_brief.md` for objective and decision lens
- `evidence_summary.txt` for condensed evidence bullets
- `summary_compact_context.txt` for child consumption

Do not put large raw datasets directly into child prompts.
