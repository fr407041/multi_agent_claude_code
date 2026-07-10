#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="${AI_COMPANY_PROJECT_ROOT:-$(pwd)}"
SOURCE="$SKILL_DIR/assets/runtime"
MANIFEST="$SOURCE/release/ai_company_package_manifest.json"

if [[ ! -f "$MANIFEST" ]]; then
  echo "PACKAGE_INTEGRITY_FAILED: bundled runtime manifest is missing: $MANIFEST" >&2
  exit 2
fi

RELEASE_ID="$(python3 - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["release_id"])
PY
)"

RUNTIME_ROOT="$PROJECT_ROOT/.ai-company/runtime"
RELEASES="$RUNTIME_ROOT/releases"
TARGET="$RELEASES/$RELEASE_ID"
STAGING="$RELEASES/.${RELEASE_ID}.tmp.$$"
mkdir -p "$RELEASES"

if [[ -d "$TARGET" ]] && ! cmp -s "$MANIFEST" "$TARGET/release/ai_company_package_manifest.json"; then
  echo "PACKAGE_INTEGRITY_FAILED: release id collision for $RELEASE_ID" >&2
  echo "Install a package with a new release_id instead of overwriting an existing release." >&2
  exit 2
fi

if [[ ! -d "$TARGET" ]]; then
  mkdir -p "$STAGING"
  cp -a "$SOURCE/." "$STAGING/"
  python3 "$STAGING/scripts/verify_install.py" \
    --strict --json --profile runtime --root "$STAGING" >/dev/null
  mv "$STAGING" "$TARGET"
fi

python3 "$TARGET/scripts/verify_install.py" \
  --strict --json --profile runtime --root "$TARGET" >/dev/null
ln -sfn "releases/$RELEASE_ID" "$RUNTIME_ROOT/current"

echo "AI-company runtime installed"
echo "release_id=$RELEASE_ID"
echo "runtime=$RUNTIME_ROOT/current"
