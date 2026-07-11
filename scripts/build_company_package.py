from __future__ import annotations

import json
import shutil
from pathlib import Path

from package_contract import (
    MANIFEST_PATH,
    RUNTIME_REQUIRED_PATHS,
    build_manifest,
    verify_package,
)


ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = ROOT / ".claude" / "skills" / "research-task-orchestrator"
RUNTIME_DEST = SKILL_ROOT / "assets" / "runtime"


def copy_file(source_root: Path, destination_root: Path, relative: str) -> None:
    source = source_root / relative
    if not source.is_file():
        raise FileNotFoundError(f"required package file is missing: {relative}")
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> int:
    if RUNTIME_DEST.exists():
        shutil.rmtree(RUNTIME_DEST)
    RUNTIME_DEST.mkdir(parents=True)
    for relative in RUNTIME_REQUIRED_PATHS:
        copy_file(ROOT, RUNTIME_DEST, relative)

    manifest = build_manifest(ROOT, SKILL_ROOT)
    root_manifest = ROOT / MANIFEST_PATH
    root_manifest.parent.mkdir(parents=True, exist_ok=True)
    root_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_manifest = RUNTIME_DEST / MANIFEST_PATH
    runtime_manifest.parent.mkdir(parents=True, exist_ok=True)
    runtime_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    repo_report = verify_package(ROOT, profile="repo", skill_root=SKILL_ROOT)
    runtime_report = verify_package(RUNTIME_DEST, profile="runtime")
    report = {
        "repo": repo_report,
        "runtime": runtime_report,
        "runtime_destination": str(RUNTIME_DEST),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if repo_report["passed"] and runtime_report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
