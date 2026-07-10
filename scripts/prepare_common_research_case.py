from __future__ import annotations

import json
import sys
from pathlib import Path


def load_text(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f"Required file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: prepare_common_research_case.py <case_dir>")

    case_dir = Path(sys.argv[1]).resolve()
    case_dir.mkdir(parents=True, exist_ok=True)

    brief_path = case_dir / "research_brief.md"
    evidence_path = case_dir / "evidence_summary.txt"
    requirements_path = case_dir / "artifact_requirements.json"

    brief = load_text(brief_path)
    evidence = load_text(evidence_path)
    requirements = json.loads(load_text(requirements_path))

    evidence_lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    if len(evidence_lines) < 3:
        raise SystemExit("evidence_summary.txt must contain at least 3 non-empty lines")

    required_keywords = requirements.get("required_keywords_all", [])
    keyword_line = ", ".join(required_keywords) if required_keywords else "None"

    summary_context = "\n".join(
        [
            "Research brief:",
            brief,
            "",
            "Evidence summary:",
            *evidence_lines,
            "",
            f"Required keywords: {keyword_line}",
        ]
    ).strip()

    compact_lines = evidence_lines[:8]
    compact_context = "\n".join(
        [
            brief.splitlines()[0] if brief.splitlines() else brief,
            *compact_lines,
            f"Required keywords: {keyword_line}",
        ]
    ).strip()

    (case_dir / "summary_context.txt").write_text(summary_context + "\n", encoding="utf-8")
    (case_dir / "summary_compact_context.txt").write_text(compact_context + "\n", encoding="utf-8")

    report = {
        "case_dir": str(case_dir),
        "evidence_line_count": len(evidence_lines),
        "required_keywords_count": len(required_keywords),
        "all_passed": True,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
