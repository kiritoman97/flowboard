from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return str(os.getenv("LPV_STORAGE_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}


def object_key(*, run_id: str, product_id: str, concept_id: str, scene_key: str, suffix: str = ".mp4") -> str:
    prefix = os.getenv("LPV_STORAGE_PREFIX", "lpv-flowboard").strip().strip("/")
    clean_run = _clean_part(run_id or "manual")
    clean_product = _clean_part(product_id or "unknown-product")
    clean_concept = _clean_part(concept_id or "default")
    clean_scene = _clean_part(scene_key or "S00")
    return f"{prefix}/runs/{clean_run}/videos/{clean_product}/{clean_concept}/{clean_scene}{suffix}"


def upload_file(path: Path, *, key: str, content_type: str = "video/mp4") -> dict[str, Any]:
    if not enabled():
        return {"enabled": False, "object_key": None, "url": None, "error": None}
    if not path.exists() or not path.is_file():
        return {"enabled": True, "object_key": key, "url": None, "error": "local_file_missing"}

    endpoint = os.getenv("LPV_STORAGE_ENDPOINT", "").strip()
    bucket = os.getenv("LPV_STORAGE_BUCKET", "").strip()
    access_key = os.getenv("LPV_STORAGE_ACCESS_KEY", "").strip()
    secret_key = os.getenv("LPV_STORAGE_SECRET_KEY", "").strip()
    if not endpoint or not bucket or not access_key or not secret_key:
        return {"enabled": True, "object_key": key, "url": None, "error": "storage_not_configured"}

    try:
        import boto3
        from botocore.config import Config
    except Exception as exc:  # noqa: BLE001
        return {"enabled": True, "object_key": key, "url": None, "error": f"boto3_unavailable:{exc}"}

    region = os.getenv("LPV_STORAGE_REGION", "us-east-1")
    force_path_style = str(os.getenv("LPV_STORAGE_FORCE_PATH_STYLE", "true")).strip().lower() in {"1", "true", "yes", "on"}
    ttl = int(os.getenv("LPV_STORAGE_SIGNED_URL_TTL_SECONDS", "86400") or "86400")
    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(s3={"addressing_style": "path" if force_path_style else "virtual"}),
        )
        client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=ttl,
        )
        return {"enabled": True, "object_key": key, "url": url, "error": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("LPV storage upload failed for %s: %s", key, exc)
        return {"enabled": True, "object_key": key, "url": None, "error": str(exc)[:500]}


def _clean_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value).strip())
    return cleaned.strip("-") or "unknown"
