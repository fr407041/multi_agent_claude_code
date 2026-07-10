from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable


DEFAULTS_RELATIVE = Path("configs") / "ai_company" / "task_harness.defaults.json"


def estimate_tokens_from_chars(char_count: int) -> int:
    return int(math.ceil(max(0, char_count) / 4))


def _defaults_path() -> Path:
    candidates = []
    configured = os.environ.get("AI_COMPANY_DEFAULTS_FILE", "").strip()
    if configured:
        candidates.append(Path(configured))
    workspace = os.environ.get("AI_COMPANY_WORKSPACE_ROOT", "").strip()
    if workspace:
        candidates.append(Path(workspace) / DEFAULTS_RELATIVE)
    candidates.extend([Path.cwd() / DEFAULTS_RELATIVE, Path(__file__).resolve().parents[1] / DEFAULTS_RELATIVE])
    return next((path for path in candidates if path.exists()), candidates[0])


def load_context_defaults() -> dict[str, Any]:
    path = _defaults_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def context_budget_for(role: str, defaults: dict[str, Any] | None = None) -> dict[str, int]:
    defaults = defaults or load_context_defaults()
    budgets = defaults.get("role_context_budgets", {})
    role_budget = budgets.get(role) or budgets.get("default") or {}
    return {
        "input_tokens": int(role_budget.get("input_tokens", defaults.get("max_context_tokens_per_turn", 12000))),
        "output_tokens": int(role_budget.get("output_tokens", defaults.get("max_output_tokens_per_turn", 2048))),
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _read_window(path: Path, start: int, size: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(max(0, start))
        return handle.read(max(0, size))


def _chunk_ranges(size: int, chunk_bytes: int, max_chunks: int) -> list[tuple[int, int]]:
    if size <= 0:
        return [(0, 0)]
    chunk_bytes = max(1, chunk_bytes)
    max_chunks = max(1, max_chunks)
    if size <= chunk_bytes * max_chunks:
        return [(start, min(size, start + chunk_bytes)) for start in range(0, size, chunk_bytes)]

    head_count = max(1, max_chunks // 2)
    tail_count = max(0, max_chunks - head_count)
    ranges = [(index * chunk_bytes, min(size, (index + 1) * chunk_bytes)) for index in range(head_count)]
    tail_start = max(ranges[-1][1], size - tail_count * chunk_bytes)
    tail_ranges = [(start, min(size, start + chunk_bytes)) for start in range(tail_start, size, chunk_bytes)]
    return sorted(set(ranges + tail_ranges))[:max_chunks]


def inspect_file(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": str(exc)}
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file(),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }


def load_file_context(
    path: Path,
    *,
    max_context_tokens: int,
    soft_limit_bytes: int,
    hard_limit_bytes: int,
    chunk_chars: int,
    overlap_chars: int,
    max_chunks: int,
) -> dict[str, Any]:
    info = inspect_file(path)
    if not info.get("exists") or not info.get("is_file"):
        return {**info, "status": "missing", "text": "", "chunks": []}
    size = int(info["size_bytes"])
    if size > hard_limit_bytes:
        return {
            **info,
            "status": "INPUT_FILE_TOO_LARGE",
            "text": "",
            "chunks": [],
            "skipped_bytes": size,
            "estimated_tokens": estimate_tokens_from_chars(size),
        }

    chunk_bytes = max(256, int(chunk_chars))
    ranges = _chunk_ranges(size, chunk_bytes, max_chunks)
    chunks: list[dict[str, Any]] = []
    remaining_chars = max(0, max_context_tokens * 4)
    for chunk_id, (start, end) in enumerate(ranges):
        if remaining_chars <= 0:
            break
        read_start = max(0, start - (overlap_chars if chunk_id else 0))
        data = _read_window(path, read_start, end - read_start)
        text = _decode(data)
        if len(text) > remaining_chars:
            text = text[:remaining_chars]
        if not text:
            continue
        chunks.append(
            {
                "chunk_id": chunk_id,
                "byte_start": read_start,
                "byte_end": read_start + len(data),
                "char_count": len(text),
                "estimated_tokens": estimate_tokens_from_chars(len(text)),
                "sha256": _sha256_bytes(data),
                "text": text,
            }
        )
        remaining_chars -= len(text)

    included_chars = sum(int(item["char_count"]) for item in chunks)
    action = "full_read" if size <= soft_limit_bytes and included_chars >= size else "chunked_context"
    return {
        **info,
        "status": "ok",
        "text": "",
        "chunks": chunks,
        "chunk_count": len(chunks),
        "included_chars": included_chars,
        "skipped_bytes": max(0, size - included_chars),
        "estimated_tokens": estimate_tokens_from_chars(included_chars),
        "source_estimated_tokens": estimate_tokens_from_chars(size),
        "context_guard_action": action,
        "soft_limit_exceeded": size > soft_limit_bytes,
    }


def build_bounded_context(
    scope: Path,
    relative_files: Iterable[str],
    *,
    role: str = "default",
    defaults: dict[str, Any] | None = None,
    extra_budget_tokens: int = 0,
) -> dict[str, Any]:
    defaults = defaults or load_context_defaults()
    budget = context_budget_for(role, defaults)
    max_tokens = max(1, budget["input_tokens"] + int(extra_budget_tokens))
    used_tokens = 0
    blocks: list[str] = []
    manifest: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for relative in relative_files:
        path = scope / str(relative)
        remaining = max(0, max_tokens - used_tokens)
        if remaining <= 0:
            manifest.append({"path": str(relative), "status": "CONTEXT_BUDGET_EXHAUSTED"})
            continue
        loaded = load_file_context(
            path,
            max_context_tokens=remaining,
            soft_limit_bytes=int(defaults.get("file_soft_limit_bytes", 262144)),
            hard_limit_bytes=int(defaults.get("file_hard_limit_bytes", 2097152)),
            chunk_chars=int(defaults.get("context_chunk_chars", 4000)),
            overlap_chars=int(defaults.get("context_chunk_overlap_chars", 200)),
            max_chunks=int(defaults.get("max_chunks_per_file", 32)),
        )
        public = {key: value for key, value in loaded.items() if key not in {"text", "chunks"}}
        manifest.append(public)
        if loaded.get("status") != "ok":
            errors.append(public)
            continue
        for chunk in loaded.get("chunks", []):
            block = f"File: {relative} [bytes {chunk['byte_start']}..{chunk['byte_end']}]\n{chunk['text']}"
            blocks.append(block)
            used_tokens += int(chunk.get("estimated_tokens", 0))
    return {
        "text": "\n\n".join(blocks) or "No target file content exists within the context budget.",
        "manifest": manifest,
        "errors": errors,
        "estimated_input_tokens": estimate_tokens_from_chars(sum(len(block) for block in blocks)),
        "context_budget_tokens": max_tokens,
        "context_budget_exceeded": bool(errors and any(item.get("status") == "CONTEXT_BUDGET_EXHAUSTED" for item in manifest)),
        "skipped_bytes": sum(int(item.get("skipped_bytes", 0)) for item in manifest),
        "context_guard_actions": sorted({str(item.get("context_guard_action")) for item in manifest if item.get("context_guard_action")}),
    }
