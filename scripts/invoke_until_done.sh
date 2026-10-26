#!/usr/bin/env bash
# Invoke a Lambda over and over until it reports nothing left to do. Each run is
# capped to fit in Lambda's 15 minutes, so a first load takes several.
#   scripts/invoke_until_done.sh footnote-index '{"budget_s":720}' 12
set -euo pipefail
fn=$1
payload=$2
rounds=${3:-6}
export AWS_MAX_ATTEMPTS=1 # a retried timeout would start a second, overlapping run
for round in $(seq 1 "$rounds"); do
  aws lambda invoke --function-name "$fn" --payload "$payload" \
    --cli-binary-format raw-in-base64-out --cli-read-timeout 900 out.json > /dev/null
  left=$(python3 - <<'PY'
import json, sys
r = json.load(open("out.json"))
if "errorMessage" in r:
    sys.exit(f"Lambda failed: {r['errorMessage']}")
print(r["summary"], file=sys.stderr)
print(r["remaining"])
PY
)
  echo "round $round: $left left"
  [ "$left" = "0" ] && exit 0
done
echo "::warning::$fn still has work left after $rounds rounds; its daily run will finish it."
