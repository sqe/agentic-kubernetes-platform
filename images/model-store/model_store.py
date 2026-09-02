"""Parallel, S3-compatible model cache hydration and artifact upload."""

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import boto3
from boto3.s3.transfer import TransferConfig


def client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("MODEL_STORE_ENDPOINT") or None,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def relative_key(key: str, prefix: str) -> Path:
    relative = (
        PurePosixPath(key).relative_to(PurePosixPath(prefix)) if prefix else PurePosixPath(key)
    )
    if ".." in relative.parts or relative.is_absolute():
        raise ValueError(f"Unsafe object key: {key}")
    return Path(*relative.parts)


def download(bucket: str, prefix: str, destination: Path, workers: int) -> None:
    s3 = client()
    destination.mkdir(parents=True, exist_ok=True)
    paginator = s3.get_paginator("list_objects_v2")
    objects = [
        item
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for item in page.get("Contents", [])
        if not item["Key"].endswith("/")
    ]
    transfer = TransferConfig(max_concurrency=workers, multipart_threshold=64 * 1024 * 1024)

    def fetch(item: dict) -> None:
        target = destination / relative_key(item["Key"], prefix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == item["Size"]:
            return
        temporary = target.with_suffix(target.suffix + ".partial")
        s3.download_file(bucket, item["Key"], str(temporary), Config=transfer)
        temporary.replace(target)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, objects))
    (destination / ".model-store-manifest.json").write_text(
        json.dumps({"bucket": bucket, "prefix": prefix, "objects": len(objects)})
    )


def upload(bucket: str, prefix: str, source: Path, workers: int) -> None:
    s3 = client()
    files = [path for path in source.rglob("*") if path.is_file()]
    transfer = TransferConfig(max_concurrency=workers, multipart_threshold=64 * 1024 * 1024)

    def send(path: Path) -> None:
        key = str(PurePosixPath(prefix) / PurePosixPath(path.relative_to(source).as_posix()))
        s3.upload_file(str(path), bucket, key, Config=transfer)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(send, files))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("download", "upload"))
    parser.add_argument("--bucket", default=os.getenv("MODEL_STORE_BUCKET"), required=False)
    parser.add_argument("--prefix", default=os.getenv("MODEL_STORE_PREFIX", ""))
    parser.add_argument("--path", type=Path, default=Path(os.getenv("MODEL_STORE_PATH", "/models")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("MODEL_STORE_WORKERS", "16")))
    parser.add_argument("--wait-for", type=Path)
    parser.add_argument("--wait-timeout", type=int, default=86400)
    args = parser.parse_args()
    if not args.bucket:
        parser.error("--bucket or MODEL_STORE_BUCKET is required")
    if args.mode == "download":
        download(args.bucket, args.prefix, args.path, args.workers)
    else:
        if args.wait_for:
            deadline = time.monotonic() + args.wait_timeout
            while not args.wait_for.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for {args.wait_for}")
                time.sleep(5)
        upload(args.bucket, args.prefix, args.path, args.workers)


if __name__ == "__main__":
    main()
