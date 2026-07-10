# Test results

- Unit tests: 42 passed, 0 failed.
- Backend API tests: 11 passed, 0 failed.
- Historical fixture frontend production build: passed in 18.4 seconds; this is not current checkout verification.
- Historical fixture dashboard smoke test: backend `/health` returned 200 and frontend returned 200; the current public checkout does not launch dashboard runtime.
- Live router smoke test: 4 of 5 prompts succeeded; 1 returned a partial response that was retried successfully.
- Failure drill: deleting one status file produced `MISSING_STATUS_FILE` and watchdog generated a fallback failure status.
- Known test gap: no long-duration 8-hour soak test has been completed.
- Known test gap: strict profile violation seed test was run only in mock mode, not live mode.
