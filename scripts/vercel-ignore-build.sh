#!/usr/bin/env bash
set -euo pipefail

changed_files="$(git diff --name-only HEAD^ HEAD 2>/dev/null || git show --name-only --pretty=format: HEAD)"

if [ -z "$changed_files" ]; then
  exit 1
fi

if echo "$changed_files" | grep -vE '^site/data/(latest|history)\.json$' >/dev/null; then
  exit 1
fi

echo "Only site/data JSON changed; skipping Vercel build. The dashboard reads GitHub raw data as the live source."
exit 0
