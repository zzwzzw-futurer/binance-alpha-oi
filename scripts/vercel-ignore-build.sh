#!/usr/bin/env bash
set -u

data_commit_message="${DATA_SYNC_COMMIT_MESSAGE:-Update public scan data}"
commit_message="${VERCEL_GIT_COMMIT_MESSAGE:-}"

if [ "$commit_message" = "$data_commit_message" ]; then
  echo "Data sync commit detected; skipping Vercel build. The dashboard reads GitHub raw data as the live source."
  exit 0
fi

changed_files=""
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  changed_files="$(git diff --name-only HEAD^ HEAD 2>/dev/null || true)"
  if [ -z "$changed_files" ]; then
    changed_files="$(git show --name-only --pretty=format: HEAD 2>/dev/null || true)"
  fi
fi

if [ -z "$changed_files" ]; then
  echo "Could not determine changed files; continuing Vercel build."
  exit 1
fi

if echo "$changed_files" | grep -vE '^site/data/(latest|history)\.json$' >/dev/null; then
  echo "Code or site shell changed; continuing Vercel build."
  exit 1
fi

echo "Only site/data JSON changed; skipping Vercel build. The dashboard reads GitHub raw data as the live source."
exit 0
