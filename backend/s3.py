"""Thin S3 uploader for generated assets."""

from __future__ import annotations

import os
import uuid

import boto3


def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY"],
        aws_secret_access_key=os.environ["AWS_SECRET_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def upload_image(image_bytes: bytes, ext: str = "png") -> str:
    """Upload PNG bytes to S3 under landing-builder/<uuid>.<ext>. Returns the public URL."""
    bucket = os.environ["S3_BUCKET"]
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    key = f"landing-builder/{uuid.uuid4().hex}.{ext}"
    _client().put_object(
        Bucket=bucket,
        Key=key,
        Body=image_bytes,
        ContentType=f"image/{ext}",
        CacheControl="public, max-age=31536000, immutable",
    )
    if region == "us-east-1":
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
