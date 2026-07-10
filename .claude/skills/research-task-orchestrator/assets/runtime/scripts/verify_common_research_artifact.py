from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PLACEHOLDERS = ("todo", "pending", "tbd", "placeholder")


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def verify_case_dir(case_dir: Path) -> dict:
    case_dir = Path(case_dir).resolve()
    summary_path = case_dir / "summary.md"
    requirements_path = case_dir / "artifact_requirements.json"

    summary = summary_path.read_text(encoding="utf-8").strip() if summary_path.exists() else ""
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))

    word_count = count_words(summary)
    bullet_count = len(re.findall(r"(?m)^\s*[-*]\s+", summary))
    lowered = summary.lower()

    checks: dict[str, bool] = {
        "summary_file_exists": summary_path.exists(),
        "not_placeholder": bool(summary) and not any(token in lowered for token in PLACEHOLDERS),
        "min_words_met": word_count >= int(requirements.get("min_words", 40)),
        "max_words_met": word_count <= int(requirements.get("max_words", 180)),
        "min_bullets_met": bullet_count >= int(requirements.get("min_bullets", 3)),
    }
    non_bullet_lines = [
        line.strip()
        for line in summary.splitlines()
        if line.strip() and not re.match(r"^\s*[-*]\s+", line) and not line.lstrip().startswith("#")
    ]

    min_numeric_mentions = requirements.get("min_numeric_mentions")
    if min_numeric_mentions is not None:
        numeric_mentions = re.findall(r"\b\d+(?:\.\d+)?%?\b", summary)
        checks["min_numeric_mentions_met"] = len(numeric_mentions) >= int(min_numeric_mentions)

    if requirements.get("require_conclusion_line") is True:
        checks["conclusion_line_present"] = bool(non_bullet_lines)

    takeaway_pattern = requirements.get("takeaway_pattern")
    if takeaway_pattern:
        checks["takeaway_present"] = re.search(takeaway_pattern, summary, flags=re.IGNORECASE) is not None

    required_keywords_all = requirements.get("required_keywords_all", [])
    for keyword in required_keywords_all:
        checks[f"keyword:{keyword}"] = keyword.lower() in lowered

    required_patterns = requirements.get("required_patterns", [])
    for pattern in required_patterns:
        checks[f"pattern:{pattern}"] = re.search(pattern, summary, flags=re.IGNORECASE) is not None

    score = round(sum(1 for ok in checks.values() if ok) / len(checks), 3) if checks else 1.0
    payload = {
        "summary_file": str(summary_path),
        "score": score,
        "all_passed": all(checks.values()) if checks else True,
        "checks": checks,
        "metrics": {
            "word_count": word_count,
            "bullet_count": bullet_count,
            "char_count": len(summary),
        },
    }
    return payload


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_common_research_artifact.py <case_dir>")

    case_dir = Path(sys.argv[1]).resolve()
    payload = verify_case_dir(case_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
