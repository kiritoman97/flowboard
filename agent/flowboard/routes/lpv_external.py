from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request as FastAPIRequest
from pydantic import BaseModel, Field
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, BoardFlowProject, ExternalLpvVideoJob, Request
from flowboard.routes.upload import UrlUploadBody, upload_image_from_url
from flowboard.services import lpv_storage, media as media_service
from flowboard.services.flow_sdk import get_flow_sdk, is_valid_project_id
from flowboard.worker.processor import get_worker

router = APIRouter(prefix="/api/external/lpv", tags=["external-lpv"])

DEFAULT_BOARD_NAME = "LPV Food Prompt Runs"


class LpvI2VCreate(BaseModel):
    job_key: str = Field(min_length=1, max_length=160)
    image_url: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    run_id: str = "manual"
    product_id: str = ""
    concept_id: str = ""
    scene_key: str = ""
    board_name: str = DEFAULT_BOARD_NAME
    aspect_ratio: str = "VIDEO_ASPECT_RATIO_PORTRAIT"
    video_quality: str = "fast"
    store_to_minio: bool = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/i2v")
async def create_lpv_i2v_job(body: LpvI2VCreate):
    board_id, project_id = await _ensure_board_and_project(body.board_name)
    upload = await upload_image_from_url(UrlUploadBody(url=body.image_url, project_id=project_id, node_id=None))
    uploaded_media_id = upload.get("media_id")
    if not isinstance(uploaded_media_id, str) or not uploaded_media_id:
        raise HTTPException(status_code=502, detail="upload returned no media_id")

    with get_session() as s:
        req = Request(
            type="gen_video",
            params={
                "prompt": body.prompt,
                "project_id": project_id,
                "start_media_id": uploaded_media_id,
                "aspect_ratio": body.aspect_ratio,
                "video_quality": body.video_quality,
            },
            status="queued",
        )
        s.add(req)
        s.commit()
        s.refresh(req)
        job = ExternalLpvVideoJob(
            job_key=body.job_key,
            run_id=body.run_id,
            product_id=body.product_id,
            concept_id=body.concept_id,
            scene_key=body.scene_key,
            board_id=board_id,
            flow_project_id=project_id,
            uploaded_media_id=uploaded_media_id,
            request_id=req.id,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = job.id
        request_id = req.id

    assert job_id is not None and request_id is not None
    get_worker().enqueue(request_id)
    return {
        "job_id": job_id,
        "job_key": body.job_key,
        "status": "queued",
        "request_id": request_id,
        "uploaded_media_id": uploaded_media_id,
        "flow_project_id": project_id,
    }


@router.get("/i2v/{job_id}")
async def get_lpv_i2v_job(job_id: int, request: FastAPIRequest):
    with get_session() as s:
        job = s.get(ExternalLpvVideoJob, job_id)
        if job is None:
            raise HTTPException(404, "LPV job not found")
        req = s.get(Request, job.request_id) if job.request_id is not None else None
        if req is None:
            raise HTTPException(404, "request not found")
        job_snapshot = job
        req_snapshot = req

    await _finalize_storage_if_ready(job_snapshot, req_snapshot)
    with get_session() as s:
        job = s.get(ExternalLpvVideoJob, job_id)
        req = s.get(Request, job.request_id) if job and job.request_id is not None else None
        assert job is not None and req is not None
        media_id = _first_media_id(req.result)
        base_url = str(request.base_url).rstrip("/")
        return {
            "job_id": job.id,
            "job_key": job.job_key,
            "status": req.status,
            "request_id": req.id,
            "flow_project_id": job.flow_project_id,
            "uploaded_media_id": job.uploaded_media_id,
            "flow_media_id": media_id,
            "flow_media_url": f"{base_url}/media/{media_id}" if media_id else None,
            "storage_object_key": job.storage_object_key,
            "storage_url": job.storage_url,
            "storage_error": job.storage_error,
            "slot_errors": (req.result or {}).get("slot_errors"),
            "partial_error": (req.result or {}).get("partial_error"),
            "error": req.error,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
        }


@router.post("/i2v/{job_id}/cancel")
def cancel_lpv_i2v_job(job_id: int):
    with get_session() as s:
        job = s.get(ExternalLpvVideoJob, job_id)
        if job is None:
            raise HTTPException(404, "LPV job not found")
        if job.request_id is None:
            raise HTTPException(409, "job has no request")
        req = s.get(Request, job.request_id)
        if req is None:
            raise HTTPException(404, "request not found")
        if req.status not in ("queued", "running"):
            raise HTTPException(409, f"only queued or running jobs can be canceled (status={req.status})")
        req.status = "canceled"
        req.error = "canceled"
        req.finished_at = _now()
        job.updated_at = _now()
        s.add(req)
        s.add(job)
        s.commit()
        return {"job_id": job.id, "request_id": req.id, "status": "canceled"}


async def _ensure_board_and_project(board_name: str) -> tuple[int, str]:
    name = (board_name or DEFAULT_BOARD_NAME).strip() or DEFAULT_BOARD_NAME
    with get_session() as s:
        board = s.exec(select(Board).where(Board.name == name)).first()
        if board is None:
            board = Board(name=name)
            s.add(board)
            s.commit()
            s.refresh(board)
        assert board.id is not None
        row = s.get(BoardFlowProject, board.id)
        if row is not None:
            return board.id, row.flow_project_id
        board_id = board.id

    resp = await get_flow_sdk().create_project(title=name)
    if resp.get("error"):
        raise HTTPException(status_code=502, detail={"message": resp["error"], "raw": resp.get("raw")})
    project_id = resp.get("project_id")
    if not isinstance(project_id, str) or not is_valid_project_id(project_id):
        raise HTTPException(status_code=502, detail={"message": "invalid project_id from Flow", "raw": resp.get("raw")})
    with get_session() as s:
        existing = s.get(BoardFlowProject, board_id)
        if existing is not None:
            return board_id, existing.flow_project_id
        s.add(BoardFlowProject(board_id=board_id, flow_project_id=project_id))
        s.commit()
    return board_id, project_id


async def _finalize_storage_if_ready(job: ExternalLpvVideoJob, req: Request) -> None:
    if req.status != "done" or job.storage_object_key or job.storage_error:
        return
    media_id = _first_media_id(req.result)
    if not media_id:
        return
    cached = media_service.cached_path(media_id)
    if cached is None:
        await media_service.fetch_and_cache(media_id)
        cached = media_service.cached_path(media_id)
    if cached is None:
        return
    key = lpv_storage.object_key(
        run_id=job.run_id,
        product_id=job.product_id,
        concept_id=job.concept_id,
        scene_key=job.scene_key,
        suffix=cached.suffix or ".mp4",
    )
    result = lpv_storage.upload_file(cached, key=key, content_type=media_service._mime_from_ext(cached.suffix))
    with get_session() as s:
        current = s.get(ExternalLpvVideoJob, job.id)
        if current is None:
            return
        current.flow_media_id = media_id
        current.updated_at = _now()
        if result.get("enabled"):
            current.storage_object_key = result.get("object_key")
            current.storage_url = result.get("url")
            current.storage_error = result.get("error")
        s.add(current)
        s.commit()


def _first_media_id(result: dict | None) -> Optional[str]:
    if not isinstance(result, dict):
        return None
    media_ids = result.get("media_ids")
    if isinstance(media_ids, list):
        for item in media_ids:
            if isinstance(item, str) and item:
                return item
    entries = result.get("media_entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("media_id"), str):
                return entry["media_id"]
    return None
