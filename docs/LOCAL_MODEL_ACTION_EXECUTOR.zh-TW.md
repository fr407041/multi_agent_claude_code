# Local Model Action Executor

## 目的

`run_local_model_action_executor.py` 用來處理 local open-source model 在 live mode 常見的問題：模型會「說」它建立了檔案或執行了工具，但實際上沒有產生 side effect。

這個 executor 的設計是：

- 開源模型只負責產生嚴格 JSON action manifest。
- runner 只執行 allowlisted actions。
- 所有檔案都限制在 run worktree 內。
- 所有輸出都產生 dashboard/watchdog 可讀的 artifacts。
- 不需要 PTT 或外部網站作為公司驗收條件。

這不是讓 Codex 代替模型做判斷；模型仍然決定要寫什麼程式、讀什麼檔案、跑什麼命令。runner 只做邊界檢查、確定 side effect 真實發生、並產生可追蹤 artifacts。

## Action Manifest

模型必須回傳純 JSON，不要 markdown，不要解釋文字。

```json
{
  "actions": [
    {
      "type": "write_file",
      "path": "probe.txt",
      "content": "hello\n"
    },
    {
      "type": "run_command",
      "command": ["python3", "-c", "from pathlib import Path; assert Path('probe.txt').exists()"],
      "timeout_sec": 60
    },
    {
      "type": "finish",
      "summary": "probe file created and verified",
      "artifacts": ["probe.txt"]
    }
  ]
}
```

允許的 action：

- `write_file`
- `read_file`
- `run_command`
- `finish`

## 安全限制

- 所有 path 必須是相對路徑。
- 所有 path 都不能離開 run worktree。
- 不允許修改 dashboard source、repo scripts、全域設定或模型設定。
- `run_command` 只能使用 allowlisted commands。
- 不接受任意 shell script 字串。
- action 數量與 repair 次數都有上限，避免無限 loop。

## 使用方式

直接呼叫 local model 產生 manifest：

```bash
python3 scripts/run_local_model_action_executor.py --task "Create a small probe file and verify it."
```

使用既有 manifest 測 runner：

```bash
python3 scripts/run_local_model_action_executor.py \
  --task "Run manifest smoke" \
  --manifest-file tmp/action_manifest.json
```

公司/離線驗收建議使用本機 fixture，不使用 PTT 或外部網站：

```bash
python3 scripts/run_local_model_action_executor.py \
  --task "Read data/input_records.json, write analyzer.py, run it, and produce output_summary.json with total_count, passed_count, failed_count, warning_count, and average_duration_sec." \
  --seed-file tests/fixtures/local_action_executor_offline_case/input_records.json=data/input_records.json \
  --expected-artifact analyzer.py \
  --expected-artifact output_summary.json
```

`--seed-file SRC=DEST` 會把 repo 內的 fixture 複製到 run worktree，讓模型能處理固定輸入資料，不需要外網。

`--expected-artifact` 會強制檢查指定檔案是否真的被產生。若模型只說完成但沒產生檔案，runner 會判定失敗，並在 repair budget 內重新要求模型產生修正版 manifest。

`--expect-json-value FILE:PATH=VALUE` 會強制檢查 JSON artifact 內容。`PATH` 使用簡單 dot path，例如 `status_counts.passed`。數字會用數字比較，不只比對字串。

## Runner-owned domain verdict

模型寫出的 `status=pass` 或 `accuracy=1.0` 不是獨立證據。需要公司級驗收時，由 runner 使用下列參數驗證：

```bash
python3 scripts/run_local_model_action_executor.py \
  --task "Generate and validate a bounded result" \
  --result-status result.json:status --result-pass-value pass \
  --compare-json actual.json:expected.json \
  --require-json-path result.json:parsed_count \
  --invariant 'result.json:parsed_count>0'
```

`--invariant` 只支援 `== != > >= < <=` 的受限比較，不執行 Python 或 shell expression。`--schema-file` 可提供 `{ "artifact": "result.json", "schema": {...} }` 或 `{ "artifacts": { "result.json": {...} } }` 格式的最小 JSON Schema mapping。結果寫入 `ai_company/domain_verdict.json`；任一 domain defect 都會使整體 run 失敗。

範例：

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

## 輸出位置

每次執行會建立：

```text
results/ai_company_task_harness/<run-id>/
```

核心 artifacts：

```text
ai_company/task_harness_report.json
ai_company/meeting_decision.json
ai_company/reviewer_verdicts.json
ai_company/artifact_verify_report.json
ai_company/subagent_claim_ledger.json
ai_company/watchdog_report.json
results/job-001.status.json
results/job-001.action_log.json
```

重要 KPI：

- `overall_status`
- `artifact_pass_rate`
- `actual_changed_count`
- `attempt_count`
- `repair_rounds_used`
- `expected_artifact_count`
- `missing_expected_artifact_count`
- `seeded_file_count`
- `semantic_expectation_count`
- `semantic_expectation_passed_count`
- `semantic_expectation_failed_count`

## Live Mode 驗收建議

公司環境若要驗證 `Claude Code Router + qwen2.5-coder` 或其他開源模型，建議順序如下：

1. 先確認 model endpoint 可用，例如 `/v1/models` 或 `/api/tags`。
2. 再確認 CCR `/health`。
3. 再確認 CCR `/v1/messages`。
4. 最後跑 action executor 的離線 fixture case。

通過條件不是模型文字說「完成」，而是：

- `analyzer.py` 真實存在。
- `output_summary.json` 真實存在。
- `output_summary.json` 內容通過 `--expect-json-value` 語意驗證。
- `task_harness_report.json` 顯示 `overall_status=pass`。
- `missing_expected_artifact_count=0`。
- `semantic_expectation_failed_count=0`。

## 失敗處理

若模型輸出 invalid JSON、unsupported action、path traversal、非 allowlisted command、缺少 expected artifact，或 JSON 語意值不符合 expectation，runner 會 fail loudly，並把錯誤寫入 artifacts。

若啟用 repair rounds，runner 會把錯誤摘要、缺失 artifacts、失敗 command stdout/stderr 節錄回傳給模型，要求它產生更窄、更正確的 action manifest。repair 次數有上限，避免卡住。
## Schema-Aware Repair Feedback

When semantic validation fails, the runner now includes input schema hints in the next bounded repair prompt.

- Use `--seed-file SRC=DEST` for JSON fixture/input files.
- The runner copies the file into the run worktree.
- The runner infers fields such as `[].duration_sec`, `[].status`, or nested dot paths.
- If `--expect-json-value` fails, repair feedback includes `schema_context_for_repair`.
- Optional: use `--schema-file path/to/schema_or_notes.json` to provide explicit schema/context hints.

Generated artifacts:
- `ai_company/schema_context.json`
- `ai_company/artifact_verify_report.json.parsed.schema_context`
- `task_harness_report.json.kpis.schema_context_available`
- `task_harness_report.json.kpis.schema_context_source_count`

This keeps the open-source model responsible for writing the solution, while the runner gives deterministic hints about available input fields. It prevents the known broad-prompt failure where a model repeatedly uses an invented field such as `duration` when the seeded fixture actually contains `duration_sec`.
