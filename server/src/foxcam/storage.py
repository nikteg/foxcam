"""S3-compatible object storage for clips, thumbnails and (optionally) archived recordings.

Uses boto3, which speaks to AWS S3 and S3-compatible stores (Cloudflare R2, Backblaze B2,
MinIO, Hetzner, Wasabi…). Optional dependency: ``uv sync --all-packages --extra s3``.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import S3Config, Settings

log = logging.getLogger(__name__)


class StorageError(RuntimeError):
    pass


class S3Storage:
    def __init__(self, cfg: S3Config):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as e:
            raise StorageError("S3 storage needs boto3: uv sync --all-packages --extra s3") from e
        self.cfg = cfg
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            region_name=cfg.region,
            aws_access_key_id=cfg.access_key_id,
            aws_secret_access_key=cfg.secret_access_key,
            config=Config(s3={"addressing_style": cfg.addressing_style},
                          retries={"max_attempts": 3, "mode": "standard"},
                          signature_version="s3v4"),
        )

    def key(self, *parts: str) -> str:
        prefix = self.cfg.prefix.strip("/")
        rel = "/".join(p.strip("/") for p in parts if p)
        return f"{prefix}/{rel}" if prefix else rel

    def upload(self, path: Path, key: str) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            self.client.upload_file(str(path), self.cfg.bucket, key,
                                    ExtraArgs={"ContentType": content_type})
        except Exception as e:  # botocore raises many types; report one
            raise StorageError(f"upload of {key} failed: {e}") from e

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.cfg.bucket, Key=key)
        except Exception as e:
            raise StorageError(f"delete of {key} failed: {e}") from e

    def url(self, key: str, filename: str | None = None) -> str:
        """Short-lived signed GET URL; `filename` makes browsers download instead of play."""
        params: dict[str, Any] = {"Bucket": self.cfg.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url("get_object", Params=params,
                                                  ExpiresIn=self.cfg.presign_seconds)

    def check(self) -> dict[str, Any]:
        """Write, read back and delete a small object."""
        key = self.key(f".connection-test-{uuid.uuid4().hex}")
        try:
            self.client.put_object(Bucket=self.cfg.bucket, Key=key, Body=b"ok")
            body = self.client.get_object(Bucket=self.cfg.bucket, Key=key)["Body"].read()
            self.client.delete_object(Bucket=self.cfg.bucket, Key=key)
        except Exception as e:  # noqa: BLE001 - reported to the UI
            return {"ok": False, "detail": str(e)[:300]}
        return {"ok": body == b"ok", "detail": "write, read and delete succeeded"}


_cache: dict[int, S3Storage] = {}


def get_storage(settings: Settings) -> S3Storage | None:
    """The configured store, or None when S3 is disabled."""
    cfg = settings.storage.s3
    if not cfg.enabled:
        return None
    # Settings objects live for the whole process; cache one client per S3 config object.
    store = _cache.get(id(cfg))
    if store is None or store.cfg is not cfg:
        store = _cache[id(cfg)] = S3Storage(cfg)
    return store


@lru_cache(maxsize=1)
def boto3_available() -> bool:
    try:
        import boto3  # noqa: F401
    except ImportError:
        return False
    return True
