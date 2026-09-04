#!/usr/bin/env bash
# scripts/check_no_tracked_env.sh
#
# Group C item 21: Defensive check that no tracked .env-style file
# contains DJANGO_DEBUG=True.
#
# .env is gitignored; this checks the BOILERPLATE files that ARE
# tracked (.env.example, env.example) plus any other file that may
# get accidentally committed. If a tracked env file has DEBUG=True,
# the script exits 1 and the CI step fails.
#
# HACK: this is a regex check, not a structural check. A future
# maintainer who sets DJANGO_DEBUG=True in a file named .env.production
# will pass. Acceptable; the audit is for the common case.
set -euo pipefail

# Find tracked files that look like env files. Uses git ls-files to
# avoid scanning untracked local secrets.
tracked_env_files=$(git ls-files | grep -E '^\.?env(\.|$)|^\.?env\.|^\.?envrc$' || true)

if [ -z "$tracked_env_files" ]; then
    echo "OK: no tracked env files found"
    exit 0
fi

violations=$(echo "$tracked_env_files" | while read -r f; do
    if [ -f "$f" ] && grep -E '^[[:space:]]*DJANGO_DEBUG[[:space:]]*=[[:space:]]*(True|true|1|yes|TRUE|YES)[[:space:]]*$' "$f" >/dev/null 2>&1; then
        echo "  $f"
    fi
done || true)

if [ -n "$violations" ]; then
    echo "ERROR: DJANGO_DEBUG=True found in tracked env file(s):"
    echo "$violations"
    echo ""
    echo "Tracked env files must have DJANGO_DEBUG=False."
    echo "If this is intentional for a dev-only file, move it to .gitignore."
    exit 1
fi

echo "OK: no tracked env file has DJANGO_DEBUG=True"
exit 0
