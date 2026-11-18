#!/usr/bin/env bash
# usage: app.sh <tag> <command...> | app.sh <tag> script <file in this directory>   runs inside that release's checkout and virtualenv
set -euo pipefail
tag="$1"; shift
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../../src"
: "${HC_APPS:=/tmp/apps}"
cd "$HC_APPS/wt/$tag"
export PATH="$HC_APPS/venv-$tag/bin:$PATH"
export PYTHONPATH="$PWD:$here:$src"
export DJANGO_SETTINGS_MODULE=migration_safety_checker_settings
export SECRET_KEY=test-key
if [ "$1" = "script" ]; then shift; exec python "$here/$1"; fi
exec "$@"
