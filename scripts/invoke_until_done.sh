#!/usr/bin/env bash
# Run a Lambda over and over until it reports nothing left to do. Each run is
# capped to fit in Lambda's 15 minutes, so a first load takes several.
#   scripts/invoke_until_done.sh footnote-index '{"budget_s":720}' 16
#
# Each run is started without waiting on the line: a request held open that long
# gets dropped by the network. The Lambda writes its result to
# runs/{function}/{run id}.json in the bucket, and this polls for it.
set -euo pipefail
fn=$1
payload=$2
rounds=${3:-6}
bucket="footnote-data-$(aws sts get-caller-identity --query Account --output text)"
for round in $(seq 1 "$rounds"); do
  run_id="${GITHUB_RUN_ID:-local-$(date +%s)}-${GITHUB_RUN_ATTEMPT:-1}-$round"
  key="runs/$fn/$run_id.json"
  body=$(python3 -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1]) | {"run_id": sys.argv[2]}))' "$payload" "$run_id")
  aws lambda invoke --function-name "$fn" --invocation-type Event --payload "$body" \
    --cli-binary-format raw-in-base64-out /dev/null > /dev/null
  echo "round $round: started $fn ($run_id)"
  for _ in $(seq 1 100); do # Lambda stops at 15 minutes; give it 16 and a bit
    aws s3api head-object --bucket "$bucket" --key "$key" > /dev/null 2>&1 && break
    sleep 10
  done
  if ! aws s3 cp "s3://$bucket/$key" out.json --quiet; then
    echo "::error::No result from $fn after 16 minutes; it may have timed out. See /aws/lambda/$fn in CloudWatch."
    exit 1
  fi
  left=$(python3 - <<'PY'
import json, sys
r = json.load(open("out.json"))
if "error" in r:
    print(r.get("traceback", ""), file=sys.stderr)
    sys.exit(f"Lambda failed: {r['error']}")
print(r["summary"], file=sys.stderr)
print(r["remaining"])
PY
)
  echo "round $round: $left left"
  [ "$left" = "0" ] && exit 0
done
echo "::warning::$fn still has work left after $rounds rounds; its daily run will finish it."
