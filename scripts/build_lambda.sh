#!/usr/bin/env bash
# Package the Lambdas: Footnote's code plus its dependencies, built for Lambda's
# Linux x86_64 Python 3.12 (Amazon Linux 2023, so manylinux_2_28 wheels work).
# Left out: boto3, which the runtime has; packages only the command line or
# model downloads use; zstandard, which LangSmith only uses to compress traces;
# and ONNX Runtime's model-conversion tools and test suites. Lambda's limit is
# 250MB unzipped. The deploy uploads the zip through S3; it's over the 50MB
# Lambda accepts directly.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/lambda build/lambda.zip
mkdir -p build/lambda
NOT_IN_LAMBDA='boto3|botocore|s3transfer|jmespath|typer|rich|pygments|markdown-it-py|mdurl|shellingham|huggingface-hub|hf-xet|fsspec|filelock|zstandard'
uv export --frozen --no-dev --no-hashes --no-emit-project --quiet \
  | grep -vE "^(${NOT_IN_LAMBDA})==" > build/requirements.txt
uv pip install --quiet --no-deps --target build/lambda -r build/requirements.txt \
  --python-platform x86_64-manylinux_2_28 --python-version 3.12 --only-binary :all:
rm -rf build/lambda/onnxruntime/{transformers,quantization,tools}
find build/lambda -type d -name tests -prune -exec rm -rf {} +
cp -r src/footnote build/lambda/
find build/lambda -name '__pycache__' -prune -exec rm -rf {} +
(cd build/lambda && zip -qr ../lambda.zip .)
echo "build/lambda.zip: $(du -h build/lambda.zip | cut -f1), $(du -sh build/lambda | cut -f1) unzipped"
