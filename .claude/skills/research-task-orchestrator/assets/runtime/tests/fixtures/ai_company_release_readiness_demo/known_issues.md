# Known issues

## Blockers

- B1: live strict-profile violation drill has not been completed against the production router path.
- B2: the 8-hour soak test for dashboard backend stability has not been completed.

## Non-blockers

- N1: dashboard visual polish has one minor spacing issue on very narrow mobile widths.
- N2: historical trend table has no chart; it is table-only by design for the first company rollout.

## Rollback posture

- Rollback script is documented and tested in staging.
- Expected rollback time is 15 minutes.
- Previous version remains available and can read the same run artifacts.

