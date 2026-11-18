#!/usr/bin/env bash
# Generate configs for the three healthchecks release pairs with `msc init-django`.
# usage: HC_APPS=/some/dir ./generate.sh <out-dir>     (run setup.sh first)
set -euo pipefail
: "${HC_APPS:=/tmp/apps}"
out="${1:?usage: generate.sh <out-dir>}"
here="$(cd "$(dirname "$0")" && pwd)"
gen() {  # name old new tests seed write read
  msc init-django --settings hc.settings --env SECRET_KEY=msc-test \
    --old "$HC_APPS/wt/$2" --old-python "$HC_APPS/venv-$2/bin/python" \
    --new "$HC_APPS/wt/$3" --new-python "$HC_APPS/venv-$3/bin/python" \
    --tests "$4" --seed "$here/$5" --write "$here/$6" --read "$here/$7" --out "$out/$1"
}
checks="hc.api.tests.test_check_model hc.api.tests.test_create_check hc.api.tests.test_get_check"
gen control v3.2 v3.3 "$checks hc.api.tests.test_list_checks" seed_checks.py write_check.py read_checks.py
gen ping-body v3.4 v3.5 "hc.api.tests.test_ping hc.api.tests.test_get_ping_body hc.api.tests.test_get_pings hc.api.tests.test_check_model" seed.py write_ping.py read_pings.py
gen badge-key v3.9 v3.10 "$checks hc.api.tests.test_check_badge hc.api.tests.test_get_badges" seed_checks.py write_check.py read_checks.py
