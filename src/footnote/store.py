"""Where filings live: an S3 bucket in AWS, or a folder on disk for local runs and tests.

Layout:
    raw/edgar/{ticker}/{accession}/{document}    the filing exactly as the SEC served it
    raw/edgar/{ticker}/companyfacts.json         every XBRL number the company reported
    clean/filings/{ticker}/{accession}.json      the filing split into sections
    clean/catalog.json                           one entry per clean filing
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class Store(Protocol):
    def exists(self, key: str) -> bool: ...
    def get(self, key: str) -> bytes: ...
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def keys(self, prefix: str) -> list[str]: ...


class LocalStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def exists(self, key: str) -> bool:
        return (self.root / key).is_file()

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def keys(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())


class S3Store:
    def __init__(self, bucket: str, client=None):
        import boto3

        self.bucket = bucket
        self.s3 = client or boto3.client("s3")

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def keys(self, prefix: str) -> list[str]:
        found = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            found += [item["Key"] for item in page.get("Contents", [])]
        return sorted(found)


def put_json(store: Store, key: str, value: Any) -> None:
    store.put(key, json.dumps(value, indent=1, ensure_ascii=False).encode(), "application/json")


def get_json(store: Store, key: str, default: Any = None) -> Any:
    return json.loads(store.get(key)) if store.exists(key) else default
