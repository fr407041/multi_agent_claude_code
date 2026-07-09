# Local Model Action Executor

## 目的

本功能解決 local open-source model live mode 的核心問題：模型可以透過 Claude Code Router 回答文字，但不一定能可靠執行真實工具操作。Action executor 要求模型輸出嚴格 JSON action manifest，再由 rule-based runner 驗證與執行。

這不是讓 Codex 或 wrapper 幫模型寫任務內容。模型仍負責決定要寫什麼檔案、跑什麼命令、交付什麼 artifact；runner 只負責安全執行與驗證。

## 支援 action

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

目前支援：

- `write_file`
- `read_file`
- `run_command`
- `finish`

## 安全限制

- 所有 path 必須是相對路徑。
- 所有 path 必須留在 run worktree 內。
- 不允許寫 dashboard source、repo scripts 或全域設定。
- `run_command` 必須使用 allowlisted command。
- 不接受任意 shell string。
- action 數量有上限，避免無限 loop。

## 執行方式

直接呼叫模型產生 manifest：

```bash
python3 scripts/run_local_model_action_executor.py --task "Create a small probe file and verify it."
```

用既有 manifest 測試 runner：

```bash
python3 scripts/run_local_model_action_executor.py \
  --task "Run manifest smoke" \
  --manifest-file tmp/action_manifest.json
```

輸出會寫到：

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

## 判斷標準

成功不看模型是否口頭說完成，而看：

- 目標檔案是否真的建立。
- allowlisted command 是否真的執行且 exit code 為 0。
- `finish.artifacts` 宣告的檔案是否存在。
- dashboard-compatible artifacts 是否產生。

如果模型輸出 invalid JSON、unsupported action、path traversal、未 allowlist command，runner 會 fail loudly，不會假成功。
