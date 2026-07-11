#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_json(url: str, timeout: int) -> tuple[str, Any, str]:
    if not url:
        return "skipped", None, "url not configured"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except TimeoutError:
        return "timeout", None, f"timed out after {timeout}s"
    except socket.timeout:
        return "timeout", None, f"timed out after {timeout}s"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:800]
        return "http_error", None, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return "connection_error", None, str(exc.reason)
    except OSError as exc:
        return "connection_error", None, str(exc)
    try:
        return "pass", json.loads(body), ""
    except json.JSONDecodeError:
        return "malformed_json", None, body[:800]


def post_json(url: str, payload: dict[str, Any], timeout: int) -> tuple[str, Any, str]:
    if not url:
        return "skipped", None, "url not configured"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="ignore")
    except TimeoutError:
        return "timeout", None, f"timed out after {timeout}s"
    except socket.timeout:
        return "timeout", None, f"timed out after {timeout}s"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:800]
        return "http_error", None, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return "connection_error", None, str(exc.reason)
    except OSError as exc:
        return "connection_error", None, str(exc)
    if not body.strip():
        return "empty", None, "empty response body"
    try:
        return "pass", json.loads(body), ""
    except json.JSONDecodeError:
        return "malformed_json", None, body[:800]


def model_visible(payload: Any, model: str) -> bool:
    if not model:
        return isinstance(payload, dict)
    if not isinstance(payload, dict):
        return False
    models = payload.get("models") or payload.get("data") or []
    for item in models:
        if not isinstance(item, dict):
            continue
        names = {str(item.get("name", "")), str(item.get("model", "")), str(item.get("id", ""))}
        if model in names:
            return True
    return False


def classify_report(report: dict[str, Any]) -> str:
    visibility = report["model_visibility"]["status"]
    direct = report["direct_completion"]["status"]
    if visibility != "pass":
        return "MODEL_VISIBILITY_FAILED"
    if direct == "skipped":
        return "DIRECT_COMPLETION_SKIPPED"
    if direct == "pass":
        return "DIRECT_COMPLETION_PASSED"
    if direct == "timeout":
        return "MODEL_VISIBLE_BUT_COMPLETION_TIMEOUT"
    return "MODEL_VISIBLE_BUT_COMPLETION_FAILED"


def build_next_action(classification: str) -> str:
    if classification == "MODEL_VISIBLE_BUT_COMPLETION_TIMEOUT":
        return (
            "Direct provider completion timed out. Continue validating through Claude Code Router/Claude CLI, "
            "then inspect Ollama OpenAI-compatible completion, model warm-up, GPU/CPU load, num_predict, and server logs."
        )
    if classification == "MODEL_VISIBILITY_FAILED":
        return "Fix the model service endpoint or model name before relying on provider diagnostics."
    if classification == "DIRECT_COMPLETION_SKIPPED":
        return "Direct provider completion was skipped by policy; use router/Claude live gates for production validation."
    if classification == "DIRECT_COMPLETION_PASSED":
        return "Direct provider completion passed; continue with router/Claude live validation."
    return "Direct provider completion failed; treat this as provider diagnostic data unless direct completion was explicitly required."


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe model-service visibility and optional direct completion diagnostics.")
    parser.add_argument("--tags-url", default="")
    parser.add_argument("--models-url", default="")
    parser.add_argument("--chat-url", default="")
    parser.add_argument("--native-generate-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--require-direct-completion", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    started = time.time()
    visibility_status = "skipped"
    visibility_payload: Any = None
    visibility_detail = "no visibility URL configured"
    visibility_url = ""
    for candidate in [args.models_url, args.tags_url]:
        status, payload, detail = get_json(candidate, args.timeout_sec)
        if status == "pass" and model_visible(payload, args.model):
            visibility_status = "pass"
            visibility_payload = payload
            visibility_detail = ""
            visibility_url = candidate
            break
        if status != "skipped" and visibility_status == "skipped":
            visibility_status = status
            visibility_payload = payload
            visibility_detail = detail
            visibility_url = candidate

    direct_status = "skipped"
    direct_payload: Any = None
    direct_detail = "direct provider completion not requested"
    direct_url = ""
    if args.chat_url:
        direct_url = args.chat_url
        direct_status, direct_payload, direct_detail = post_json(
            args.chat_url,
            {
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with exactly: LIVE-PROVIDER-DIAGNOSTIC"}],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 16},
            },
            args.timeout_sec,
        )
    elif args.native_generate_url:
        direct_url = args.native_generate_url
        direct_status, direct_payload, direct_detail = post_json(
            args.native_generate_url,
            {
                "model": args.model,
                "prompt": "Reply with exactly: LIVE-PROVIDER-DIAGNOSTIC",
                "stream": False,
                "options": {"temperature": 0, "num_predict": 16},
            },
            args.timeout_sec,
        )

    report = {
        "provider_diagnostic_status": "complete",
        "classification": "",
        "model_visibility": {
            "status": visibility_status,
            "url": visibility_url,
            "model": args.model,
            "detail": visibility_detail,
        },
        "direct_completion": {
            "status": direct_status,
            "url": direct_url,
            "required": bool(args.require_direct_completion),
            "detail": direct_detail,
        },
        "next_action": "",
        "duration_sec": round(time.time() - started, 3),
    }
    report["classification"] = classify_report(report)
    report["next_action"] = build_next_action(report["classification"])

    if args.output:
        write_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    hard_fail = report["model_visibility"]["status"] != "pass" or (
        args.require_direct_completion and report["direct_completion"]["status"] != "pass"
    )
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
