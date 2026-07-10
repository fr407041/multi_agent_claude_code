from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
PERCENT_RE = re.compile(r"[+-]?\d+(?:\.\d+)?%")
BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)

TARGET_DATES = ("2026-03-13", "2026-05-12", "2026-07-01")
UNCERTAINTY_TOKENS = (
    "uncertain",
    "uncertainty",
    "mixed",
    "caution",
    "not necessarily",
    "may reflect",
    "cannot conclude",
    "not a clean bullish signal",
)
PRICE_TOKENS = ("price", "stock", "reaction", "1d", "5d", "20d", "rose", "fell", "gain", "drop")
INSIDER_TOKENS = (
    "insider",
    "director",
    "form 4",
    "open-market buy",
    "open market buy",
    "buy",
    "sale",
    "grant",
    "withholding",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _score(checks: dict[str, bool]) -> float:
    return round(sum(1 for ok in checks.values() if ok) / len(checks), 3) if checks else 0.0


def verify_evidence_integrity(case_dir: Path) -> dict[str, Any]:
    insider_path = case_dir / "insider_evidence.json"
    reaction_path = case_dir / "price_reaction.json"
    context_candidates = [case_dir / "summary_context.txt", case_dir / "summary_compact_context.txt"]
    context_path = next((path for path in context_candidates if path.exists()), context_candidates[0])

    insider = _read_json(insider_path) if insider_path.exists() else {}
    reactions = _read_json(reaction_path) if reaction_path.exists() else {}
    context_text = context_path.read_text(encoding="utf-8", errors="ignore") if context_path.exists() else ""

    filings = insider.get("recent_filings", []) if isinstance(insider, dict) else []
    reaction_rows = reactions.get("reactions", []) if isinstance(reactions, dict) else []
    filing_dates = {str(item.get("trade_date", "")) for item in filings if isinstance(item, dict)}
    reaction_dates = {str(item.get("trade_date", "")) for item in reaction_rows if isinstance(item, dict)}
    open_market_buy_dates = {
        str(item.get("trade_date", ""))
        for item in filings
        if isinstance(item, dict) and str(item.get("classification", "")) == "open_market_buy"
    }
    compensation_grant_dates = {
        str(item.get("trade_date", ""))
        for item in filings
        if isinstance(item, dict) and "grant" in str(item.get("classification", ""))
    }

    checks = {
        "insider_evidence_exists": insider_path.exists(),
        "price_reaction_exists": reaction_path.exists(),
        "summary_context_exists": context_path.exists(),
        "recent_filings_populated": len(filings) >= 5,
        "price_reactions_populated": len(reaction_rows) >= 5,
        "required_open_market_buy_dates_present": {"2026-03-13", "2026-05-12"}.issubset(open_market_buy_dates),
        "required_compensation_date_present": "2026-07-01" in compensation_grant_dates,
        "required_dates_have_reactions": set(TARGET_DATES).issubset(reaction_dates),
        "context_mentions_required_dates": all(date in context_text for date in TARGET_DATES),
    }
    return {
        "checks": checks,
        "score": _score(checks),
        "all_passed": all(checks.values()),
        "metrics": {
            "recent_filings_count": len(filings),
            "price_reaction_count": len(reaction_rows),
            "open_market_buy_dates": sorted(open_market_buy_dates),
            "compensation_grant_dates": sorted(compensation_grant_dates),
            "reaction_dates": sorted(reaction_dates),
        },
    }


def verify_summary_fidelity(text: str) -> dict[str, Any]:
    lowered = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullet_lines = [line for line in lines if BULLET_RE.match(line)]
    takeaway_lines = [line for line in lines if not BULLET_RE.match(line)]
    word_count = len(text.split())
    date_count = len(DATE_RE.findall(text))
    percent_count = len(PERCENT_RE.findall(text))

    required_date_presence = {date: (date in text) for date in TARGET_DATES}
    reaction_mentions = [token for token in ("1d", "5d", "20d", "price", "reaction") if token in lowered]
    uncertainty_present = any(token in lowered for token in UNCERTAINTY_TOKENS)
    compensation_caution_present = ("2026-07-01" in text) and ("grant" in lowered) and any(
        token in lowered for token in ("not necessarily", "not a clean bullish signal", "may reflect", "compensation")
    )

    checks = {
        "three_short_bullets": len(bullet_lines) == 3,
        "single_takeaway_sentence": len(takeaway_lines) == 1,
        "word_count_within_limit": 35 <= word_count <= 120,
        "has_multiple_dates": date_count >= 2,
        "has_multiple_percentages": percent_count >= 2,
        "mentions_insider_activity": any(token in lowered for token in INSIDER_TOKENS),
        "mentions_price_reaction": any(token in lowered for token in PRICE_TOKENS),
        "states_uncertainty_or_limits": uncertainty_present,
        "covers_required_dates": all(required_date_presence.values()),
        "flags_compensation_as_non_bullish": compensation_caution_present,
    }

    covered_claims = 0
    if "2026-03-13" in text and any(token in lowered for token in ("open-market buy", "open market buy", "buy")):
        covered_claims += 1
    if "2026-05-12" in text and any(token in lowered for token in ("open-market buy", "open market buy", "buy")):
        covered_claims += 1
    if compensation_caution_present:
        covered_claims += 1
    evidence_coverage_rate = round(covered_claims / 3, 3)

    dated_claim_coverage = round(sum(1 for ok in required_date_presence.values() if ok) / len(required_date_presence), 3)
    price_reaction_coverage = 1.0 if checks["mentions_price_reaction"] and percent_count >= 2 else 0.0
    uncertainty_statement_presence = 1.0 if uncertainty_present else 0.0

    return {
        "checks": checks,
        "score": _score(checks),
        "all_passed": all(checks.values()),
        "metrics": {
            "char_count": len(text),
            "word_count": word_count,
            "date_count": date_count,
            "percent_count": percent_count,
            "bullet_count": len(bullet_lines),
            "takeaway_count": len(takeaway_lines),
            "required_date_presence": required_date_presence,
            "reaction_mentions": reaction_mentions,
            "evidence_coverage_rate": evidence_coverage_rate,
            "dated_claim_coverage": dated_claim_coverage,
            "price_reaction_coverage": price_reaction_coverage,
            "uncertainty_statement_presence": uncertainty_statement_presence,
        },
    }


def classify_failure(evidence: dict[str, Any], fidelity: dict[str, Any]) -> str:
    if not evidence.get("all_passed", False):
        return "INPUT_INSUFFICIENT"
    fidelity_checks = fidelity.get("checks", {})
    if not fidelity_checks.get("three_short_bullets", False) or not fidelity_checks.get("single_takeaway_sentence", False):
        return "OUTPUT_CONTRACT_VIOLATION"
    if not fidelity_checks.get("mentions_price_reaction", False) or fidelity.get("metrics", {}).get("price_reaction_coverage", 0.0) < 1.0:
        return "MISSING_PRICE_REACTION"
    if not fidelity_checks.get("states_uncertainty_or_limits", False):
        return "MISSING_UNCERTAINTY"
    if not fidelity_checks.get("flags_compensation_as_non_bullish", False):
        return "MISSING_COMPENSATION_CAVEAT"
    if not fidelity_checks.get("covers_required_dates", False):
        return "MISSING_REQUIRED_DATES"
    if fidelity.get("score", 0.0) < 1.0:
        return "SUMMARY_FIDELITY_GAP"
    return "PASS"


def verify_case_dir(case_dir: Path) -> dict[str, Any]:
    summary_path = case_dir / "summary.md"
    text = summary_path.read_text(encoding="utf-8", errors="ignore") if summary_path.exists() else ""
    lowered = text.lower()
    evidence = verify_evidence_integrity(case_dir)
    fidelity = verify_summary_fidelity(text)

    legacy_checks = {
        "summary_file_exists": summary_path.exists(),
        "not_pending_placeholder": "pending" not in lowered,
        "has_multiple_dates": fidelity["checks"]["has_multiple_dates"],
        "has_multiple_percentages": fidelity["checks"]["has_multiple_percentages"],
        "mentions_insider_activity": fidelity["checks"]["mentions_insider_activity"],
        "mentions_price_reaction": fidelity["checks"]["mentions_price_reaction"],
        "states_uncertainty_or_limits": fidelity["checks"]["states_uncertainty_or_limits"],
        "reasonable_length": fidelity["checks"]["word_count_within_limit"],
    }
    overall_checks = {**legacy_checks, "evidence_integrity_passed": evidence["all_passed"], "summary_fidelity_passed": fidelity["all_passed"]}
    failure_category = classify_failure(evidence, fidelity)

    payload = {
        "summary_file": str(summary_path),
        "score": round((evidence["score"] + fidelity["score"]) / 2, 3),
        "all_passed": evidence["all_passed"] and fidelity["all_passed"] and legacy_checks["not_pending_placeholder"],
        "checks": overall_checks,
        "metrics": {
            **fidelity["metrics"],
            **evidence["metrics"],
        },
        "evidence_integrity": evidence,
        "summary_fidelity": fidelity,
        "failure_category": failure_category,
    }
    return payload


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: verify_sens_summary_artifact.py <case_dir>")
    case_dir = Path(sys.argv[1]).resolve()
    payload = verify_case_dir(case_dir)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
