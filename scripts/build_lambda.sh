#!/usr/bin/env bash
# Package the ingest Lambda: Footnote's code plus its dependencies, built for
# Lambda's Linux x86_64 Python 3.12. boto3 is left out; the runtime has it.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda build/lambda.zip
mkdir -p build/lambda
uv export --frozen --no-dev --no-hashes --no-emit-project --quiet \
  | grep -vE '^(boto3|botocore|s3transfer|jmespath)==' > build/requirements.txt
uv pip install --quiet --target build/lambda -r build/requirements.txt \
  --python-platform x86_64-manylinux2014 --python-version 3.12 --only-binary :all:
cp -r src/footnote build/lambda/
find build/lambda -name '__pycache__' -prune -exec rm -rf {} +
(cd build/lambda && zip -qr ../lambda.zip .)
echo "build/lambda.zip: $(du -h build/lambda.zip | cut -f1)"
