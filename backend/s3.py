"""Thin S3 uploader for generated assets."""

from __future__ import annotations

import os
import time
import uuid

import boto3

_REGION = "us-east-1"


def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY"],
        aws_secret_access_key=os.environ["AWS_SECRET_KEY"],
        region_name=_REGION,
    )


def _public_url(bucket: str, key: str) -> str:
    return f"https://{bucket}.s3.{_REGION}.amazonaws.com/{key}"


def upload_image(
    image_bytes: bytes,
    ext: str = "png",
    bucket: str | None = None,
    key_prefix: str = "landing-builder",
) -> str:
    """Upload image bytes to S3.

    Args:
        image_bytes: Raw image data.
        ext:         File extension (default "png").
        bucket:      Target bucket. Defaults to S3_BUCKET env var.
        key_prefix:  S3 key prefix (default "landing-builder").
                     For per-job isolation pass "{folder}/images".

    Returns:
        Public HTTPS URL of the uploaded object.
    """
    bucket = bucket or os.environ["S3_BUCKET"]
    key = f"{key_prefix}/{uuid.uuid4().hex}.{ext}"
    _client().put_object(
        Bucket=bucket,
        Key=key,
        Body=image_bytes,
        ContentType=f"image/{ext}",
        CacheControl="public, max-age=31536000, immutable",
    )
    return _public_url(bucket, key)


def upload_html(html: str, bucket: str, folder: str) -> str:
    """Upload the assembled HTML as {folder}/index.html.

    Returns:
        Versioned public URL:
        https://{bucket}.s3.us-east-1.amazonaws.com/{folder}/index.html?v={timestamp}
    """
    key = f"{folder}/index.html"
    _client().put_object(
        Bucket=bucket,
        Key=key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache, no-store, must-revalidate",
    )
    versioned_url = f"{_public_url(bucket, key)}?v={int(time.time())}"
    return versioned_url
