#!/usr/bin/env bash
# Download bge-small-en-v1.5's ONNX export and tokenizer into build/model, and
# note which revision of the model repository they came from.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/model
base=https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main
curl -fsSL --retry 3 -D build/model/headers.txt -o build/model/model.onnx "$base/onnx/model.onnx"
curl -fsSL --retry 3 -o build/model/tokenizer.json "$base/tokenizer.json"
grep -i '^x-repo-commit:' build/model/headers.txt | head -1 | cut -d' ' -f2 | tr -d '\r' > build/model/revision.txt
rm build/model/headers.txt
echo "build/model: bge-small-en-v1.5 at revision $(cat build/model/revision.txt), $(du -sh build/model | cut -f1)"
