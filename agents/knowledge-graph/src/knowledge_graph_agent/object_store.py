import asyncio
import hashlib
from pathlib import PurePosixPath
from typing import BinaryIO

import boto3

from platform_runtime.settings import settings


class ObjectStore:
    def __init__(self) -> None:
        self.client = boto3.client("s3", endpoint_url=settings.object_store_endpoint)
        self.bucket = settings.object_store_bucket

    async def upload(
        self,
        tenant: str,
        document_id: str,
        filename: str,
        body: BinaryIO,
        content_type: str,
    ) -> str:
        tenant_key = hashlib.sha256(tenant.encode()).hexdigest()[:24]
        safe_name = PurePosixPath(filename).name.replace(" ", "_")
        key = str(PurePosixPath("documents", tenant_key, document_id, safe_name))
        await asyncio.to_thread(
            self.client.upload_fileobj,
            body,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
        )
        return f"s3://{self.bucket}/{key}"

    async def download(self, uri: str) -> tuple[bytes, str]:
        path = uri.removeprefix("s3://")
        bucket, _, key = path.partition("/")
        if bucket != self.bucket or not key:
            raise ValueError("Object URI is outside the configured document bucket")
        response = await asyncio.to_thread(self.client.get_object, Bucket=bucket, Key=key)
        data = await asyncio.to_thread(response["Body"].read)
        return data, response.get("ContentType", "application/octet-stream")
