"""Magnet downloader API using the native Backblaze B2 API."""

import asyncio
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import libtorrent as lt
from b2sdk.v2 import B2Api, InMemoryAccountInfo
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="B2 Magnet Downloader", version="1.0.0")
jobs: Dict[str, Dict[str, Any]] = {}
DOWNLOAD_ROOT = Path(os.getenv("DOWNLOAD_DIR", "/app/downloads"))
DOWNLOAD_ROOT.mkdir(parents=True, exist_ok=True)


class DownloadRequest(BaseModel):
    magnet_link: str = Field(..., min_length=1)
    filename: Optional[str] = None
    add_to_zip: bool = True
    timeout: int = Field(3600, ge=30, le=86400)


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: float
    message: str
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(name: str, max_len: int = 200) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", (name or "").strip()).strip(".")
    return cleaned[:max_len] or "download"


def parse_magnet_info_hash(magnet_link: str) -> str:
    if not magnet_link.strip().lower().startswith("magnet:?"):
        raise ValueError("A valid magnet URI is required")
    parsed = urlparse(magnet_link)
    for value in parse_qs(parsed.query).get("xt", []):
        match = re.fullmatch(r"urn:btih:([A-Fa-f0-9]{40}|[A-Fa-f0-9]{32})", value, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    raise ValueError("Could not extract a valid BTIH hash from magnet link")


def get_b2_bucket():
    key_id = os.getenv("B2_APPLICATION_KEY_ID")
    application_key = os.getenv("B2_APPLICATION_KEY")
    bucket_name = os.getenv("B2_BUCKET_NAME")
    if not key_id or not application_key or not bucket_name:
        raise RuntimeError("B2_APPLICATION_KEY_ID, B2_APPLICATION_KEY and B2_BUCKET_NAME are required")
    info = InMemoryAccountInfo()
    api = B2Api(info)
    api.authorize_account("production", key_id, application_key)
    return api.get_bucket_by_name(bucket_name)


def upload_file(bucket, path: Path, object_name: str) -> Dict[str, str]:
    uploaded = bucket.upload_local_file(
        local_file=str(path),
        file_name=object_name,
        content_type="application/zip" if path.suffix.lower() == ".zip" else "b2/x-auto",
    )
    return {"file_id": uploaded.id_, "file_name": uploaded.file_name}


def add_log(job_id: str, message: str) -> None:
    jobs[job_id]["logs"].append({"timestamp": now(), "message": message})
    jobs[job_id]["updated_at"] = now()


def update_job(job_id: str, status: str, message: str) -> None:
    jobs[job_id]["status"] = status
    jobs[job_id]["message"] = message
    jobs[job_id]["updated_at"] = now()


async def run_download(job_id: str, request: DownloadRequest) -> None:
    work_dir: Optional[Path] = None
    try:
        info_hash = parse_magnet_info_hash(request.magnet_link)
        work_dir = DOWNLOAD_ROOT / f"download_{job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        update_job(job_id, "downloading", "Starting download...")
        add_log(job_id, f"Torrent hash: {info_hash}")
        session = lt.session()
        handle = lt.add_magnet_uri(session, request.magnet_link, {"save_path": str(work_dir), "storage_mode": lt.storage_mode_t.storage_mode_sparse})
        started = time.time()
        last_progress = -1.0
        while time.time() - started < request.timeout:
            status = handle.status()
            progress = float(status.progress * 100)
            jobs[job_id]["progress"] = round(progress, 2)
            jobs[job_id]["updated_at"] = now()
            if progress != last_progress:
                add_log(job_id, f"Progress: {progress:.1f}%")
                last_progress = progress
            if status.is_seeding:
                break
            await asyncio.sleep(5)
        else:
            raise TimeoutError("Download timeout exceeded")

        files = [path for path in work_dir.rglob("*") if path.is_file() and not path.name.startswith(".")]
        if not files:
            raise RuntimeError("Download completed but no files were found")
        bucket_name = os.getenv("B2_BUCKET_NAME")
        bucket = get_b2_bucket()
        prefix = safe_filename(os.getenv("B2_OBJECT_PREFIX", "magnet-downloads"))
        uploaded: List[Dict[str, Any]] = []
        if request.add_to_zip:
            update_job(job_id, "compressing", "Compressing to ZIP...")
            zip_name = f"{safe_filename(request.filename or f'torrent_{info_hash[:8]}')}.zip"
            zip_path = DOWNLOAD_ROOT / f"{job_id}_{zip_name}"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for path in files:
                    archive.write(path, path.relative_to(work_dir))
            update_job(job_id, "uploading", "Uploading ZIP to B2...")
            object_name = f"{prefix}/{job_id}/{zip_name}"
            b2_file = await asyncio.to_thread(upload_file, bucket, zip_path, object_name)
            uploaded.append({"filename": zip_name, "object_name": object_name, "size": zip_path.stat().st_size, **b2_file})
            zip_path.unlink(missing_ok=True)
        else:
            update_job(job_id, "uploading", "Uploading files to B2...")
            for path in files:
                object_name = f"{prefix}/{job_id}/{path.relative_to(work_dir).as_posix()}"
                b2_file = await asyncio.to_thread(upload_file, bucket, path, object_name)
                uploaded.append({"filename": path.name, "object_name": object_name, "size": path.stat().st_size, **b2_file})
        jobs[job_id]["progress"] = 100.0
        jobs[job_id]["result"] = {"bucket": bucket_name, "files": uploaded, "files_count": len(files), "download_time": time.time() - started}
        update_job(job_id, "completed", "Upload completed successfully")
        add_log(job_id, f"Uploaded {len(uploaded)} object(s) to B2")
    except Exception as error:
        update_job(job_id, "failed", str(error))
        add_log(job_id, f"Error: {error}")
    finally:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/download", response_model=JobStatus)
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    timestamp = now()
    jobs[job_id] = {"job_id": job_id, "status": "queued", "progress": 0.0, "message": "Job queued", "created_at": timestamp, "updated_at": timestamp, "logs": [], "result": None}
    background_tasks.add_task(run_download, job_id, request)
    return JobStatus(**jobs[job_id])


@app.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(**jobs[job_id])


@app.get("/logs/{job_id}")
async def get_logs(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "logs": jobs[job_id]["logs"]}


@app.get("/health")
async def health():
    active = sum(job["status"] in {"queued", "downloading", "compressing", "uploading"} for job in jobs.values())
    return {"status": "healthy", "active_jobs": active, "timestamp": now()}


@app.get("/")
async def root():
    return {"name": "B2 Magnet Downloader API", "version": "1.0.0"}
