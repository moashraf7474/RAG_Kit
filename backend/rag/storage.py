"""S3-compatible object storage for original documents.

Ported from `rag_core_lib/impl/file_services/s3_service.py`. MinIO locally,
any S3 in production — only the endpoint changes.
"""

from __future__ import annotations

import logging
from functools import lru_cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from rag.conf import get_config

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_s3_client():
    settings = get_config().s3
    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint or None,
        aws_access_key_id=settings.access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.secret_access_key.get_secret_value() or None,
        region_name=settings.region,
        # MinIO requires path-style addressing.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache(maxsize=1)
def get_public_s3_client():
    """S3 client bound to the browser-reachable endpoint for presigning.

    Pre-signed URLs must be signed against the exact host the browser will hit,
    because AWS4 signs the `Host` header. Signing against `minio:9000` and then
    rewriting the host in the URL breaks the signature.
    """
    settings = get_config().s3
    if not settings.public_endpoint or settings.public_endpoint == settings.endpoint:
        return get_s3_client()
    return boto3.client(
        "s3",
        endpoint_url=settings.public_endpoint,
        aws_access_key_id=settings.access_key_id.get_secret_value() or None,
        aws_secret_access_key=settings.secret_access_key.get_secret_value() or None,
        region_name=settings.region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket() -> None:
    """Create the bucket if missing. Idempotent."""
    settings = get_config().s3
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "403"):
            raise
        try:
            client.create_bucket(Bucket=settings.bucket)
            logger.info("Created bucket '%s'.", settings.bucket)
        except ClientError:
            logger.exception("Could not create bucket '%s'.", settings.bucket)
            raise


def upload_fileobj(fileobj, key: str, content_type: str = "application/octet-stream") -> str:
    """Store a file object and return its key."""
    ensure_bucket()
    settings = get_config().s3
    fileobj.seek(0)
    get_s3_client().upload_fileobj(
        fileobj,
        settings.bucket,
        key,
        ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"}
        if settings.endpoint and "amazonaws" in settings.endpoint
        else {"ContentType": content_type},
    )
    return key


def download_to_path(key: str, destination: str) -> str:
    settings = get_config().s3
    get_s3_client().download_file(settings.bucket, key, destination)
    return destination


def presigned_url(key: str) -> str:
    """Short-lived download link handed to the frontend for citations."""
    if not key:
        return ""
    settings = get_config().s3
    try:
        return get_public_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.bucket, "Key": key},
            ExpiresIn=settings.presign_ttl_seconds,
        )
    except ClientError:
        logger.exception("Could not presign key '%s'.", key)
        return ""


def delete(key: str) -> None:
    if not key:
        return
    settings = get_config().s3
    try:
        get_s3_client().delete_object(Bucket=settings.bucket, Key=key)
    except ClientError:
        logger.exception("Could not delete key '%s'.", key)
