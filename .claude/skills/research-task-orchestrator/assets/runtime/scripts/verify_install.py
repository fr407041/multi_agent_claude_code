#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from package_contract import RUNTIME_REQUIRED_PATHS, verify_package
from validate_ai_company_spec import validate_spec


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = Path("docs/ai_specs/ai-company-release-readiness-strict-demo.json")


def build_report(root: Path, *, strict: bool, profile: str, skill_root: Path | None) -> dict:
    root = root.resolve()
    required_missing = [rel for rel in RUNTIME_REQUIRED_PATHS if not (root / rel).is_file()]
    spec_path = root / DEFAULT_SPEC
    spec_report = validate_spec(spec_path, root) if spec_path.is_file() else {
        "passed": False,
        "spec_contract_status": "SPEC_CONTRACT_INVALID",
        "artifact_contract_status": "unknown",
        "worker_template_status": "unknown",
        "referenced_command_status": "unknown",
        "errors": [{"code": "SPEC_FILE_MISSING", "detail": DEFAULT_SPEC.as_posix()}],
    }
    package_report = verify_package(root, profile=profile, skill_root=skill_root) if strict else {
        "package_integrity": "not_checked",
        "passed": True,
        "release_id": "",
        "missing_files": [],
        "hash_mismatches": [],
    }
    missing_files = sorted(set(required_missing + list(package_report.get("missing_files", []))))
    passed = not missing_files and bool(spec_report.get("passed")) and bool(package_report.get("passed"))
    return {
        "passed": passed,
        "strict": strict,
        "profile": profile,
        "root": str(root),
        "package_integrity": package_report.get("package_integrity"),
        "release_id": package_report.get("release_id", ""),
        "spec_contract_status": spec_report.get("spec_contract_status"),
        "artifact_contract_status": spec_report.get("artifact_contract_status"),
        "worker_template_status": spec_report.get("worker_template_status"),
        "referenced_command_status": spec_report.get("referenced_command_status"),
        "missing_files": missing_files,
        "hash_mismatches": package_report.get("hash_mismatches", []),
        "spec_errors": spec_report.get("errors", []),
        "model_calls_started": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ai-company package integrity and canonical spec contract.")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--profile", choices=["repo", "runtime"], default="repo")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--skill-root", default="")
    args = parser.parse_args()

    skill_root = Path(args.skill_root).resolve() if args.skill_root else None
    report = build_report(
        Path(args.root),
        strict=bool(args.strict),
        profile=args.profile,
        skill_root=skill_root,
    )
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("multi_agent_claude_code install verification")
        print(f"root: {report['root']}")
        print(f"strict: {report['strict']}")
        print(f"package integrity: {report['package_integrity']}")
        print(f"release id: {report['release_id'] or 'not-checked'}")
        print(f"spec contract: {report['spec_contract_status']}")
        print(f"artifact contract: {report['artifact_contract_status']}")
        print(f"worker templates: {report['worker_template_status']}")
        print(f"referenced commands: {report['referenced_command_status']}")
        if report["missing_files"]:
            print("missing files:")
            for path in report["missing_files"]:
                print(f"- {path}")
        if report["hash_mismatches"]:
            print("hash mismatches:")
            for item in report["hash_mismatches"]:
                print(f"- {item['path']}")
        if report["spec_errors"]:
            print("spec errors:")
            for item in report["spec_errors"]:
                print(f"- {item['code']}: {item['detail']}")
        print("\nInstall verification passed." if report["passed"] else "\nInstall verification failed before model execution.")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
