#!/usr/bin/env bash
# Prepare checkouts and virtualenvs for the healthchecks runs.
# usage: HC_APPS=/some/dir ./setup.sh     (needs git, uv, Python 3.13)
set -euo pipefail
: "${HC_APPS:=/tmp/apps}"
mkdir -p "$HC_APPS/wt"
[ -d "$HC_APPS/healthchecks" ] || git clone -q https://github.com/healthchecks/healthchecks.git "$HC_APPS/healthchecks"
for tag in v3.2 v3.3 v3.4 v3.5 v3.9 v3.10; do
  [ -d "$HC_APPS/wt/$tag" ] || git -C "$HC_APPS/healthchecks" worktree add -f "$HC_APPS/wt/$tag" "$tag" >/dev/null 2>&1
  req="$HC_APPS/req-$tag.txt"
  # pycurl is installed unpinned (the pinned build needs libcurl headers);
  # psycopg2 and pydantic pins of the older releases have no Python 3.13 wheels.
  grep -vi '^pycurl' "$HC_APPS/wt/$tag/requirements.txt" \
    | sed 's/^psycopg2==.*/psycopg2-binary==2.9.10/; s/^pydantic==2\.[0-7]\..*/pydantic==2.8.2/' > "$req"
  uv venv -q --python 3.13 "$HC_APPS/venv-$tag"
  uv pip install -q --python "$HC_APPS/venv-$tag/bin/python" -r "$req" pycurl
done
echo "ready in $HC_APPS"
