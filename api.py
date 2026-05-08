"""FastAPI application — document ingestion API."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from pipeline import db as pipeline_db
from pipeline.orchestrator import run_pipeline
from pipeline.stage1_upload import upload_and_validate

logger = logging.getLogger("api")

app = FastAPI(title="HOA Compliance AI — Ingestion API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _run_pipeline_bg(
    raw_path: str,
    doc_id: str,
    hoa_id: str,
    hoa_name: str,
    document_type: str,
    effective_date: Optional[str],
    supersedes_doc_id: Optional[str],
    supersedes_doc_type: Optional[str],
) -> None:
    """Run stages 2-5 in a thread pool. Stage 1 will be a manifest cache hit."""
    try:
        await asyncio.to_thread(
            run_pipeline,
            source_pdf_path=raw_path,
            hoa_id=hoa_id,
            hoa_name=hoa_name,
            document_type=document_type,
            effective_date=effective_date,
            supersedes_doc_id=supersedes_doc_id,
            supersedes_doc_type=supersedes_doc_type,
        )
    except Exception as e:
        logger.error("pipeline_bg_failed doc_id=%s error=%s", doc_id, e)
        pipeline_db.update_document_status(doc_id, "failed")


@app.post("/api/v1/documents", status_code=202)
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    hoa_id: str = Form(...),
    hoa_name: str = Form(...),
    document_type: str = Form(...),
    effective_date: Optional[str] = Form(None),
    supersedes_doc_id: Optional[str] = Form(None),
    supersedes_doc_type: Optional[str] = Form(None),
):
    """
    Upload a governance PDF. Returns 202 with doc_id immediately after
    validation; stages 2-5 run in the background. Poll GET /api/v1/documents/{doc_id}
    for status updates.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # Save upload preserving the original filename (stage 1 uses it as the manifest key)
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / file.filename
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        # Stage 1 runs synchronously: validate, copy to raw dir, assign doc_id
        stage1 = upload_and_validate(
            source_path=tmp_path,
            hoa_id=hoa_id,
            document_type=document_type,
            effective_date=effective_date,
            supersedes_doc_id=supersedes_doc_id,
        )
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=str(e))

    doc_id = stage1["doc_id"]
    raw_path = stage1["raw_path"]

    # Temp upload no longer needed — stage 1 already copied it to raw dir
    shutil.rmtree(tmp_dir, ignore_errors=True)

    # Persist HOA + document record immediately so the caller can poll status
    pipeline_db.upsert_hoa(hoa_id, hoa_name)
    pipeline_db.insert_document(
        doc_id=doc_id,
        hoa_id=hoa_id,
        original_filename=file.filename,
        document_type=document_type,
        raw_path=raw_path,
        page_count=stage1["page_count"],
        effective_date=effective_date,
        supersedes_doc_id=supersedes_doc_id,
    )

    # Stages 2-5 run in a background thread (stage 1 will hit manifest cache)
    background_tasks.add_task(
        _run_pipeline_bg,
        raw_path=raw_path,
        doc_id=doc_id,
        hoa_id=hoa_id,
        hoa_name=hoa_name,
        document_type=document_type,
        effective_date=effective_date,
        supersedes_doc_id=supersedes_doc_id,
        supersedes_doc_type=supersedes_doc_type,
    )

    return {"doc_id": doc_id, "status": "queued"}


@app.get("/api/v1/documents/{doc_id}")
async def get_document(doc_id: str):
    """Return current status and metadata for a document."""
    doc = pipeline_db.get_document(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.get("/api/v1/hoas")
async def list_hoas():
    """Return all registered HOAs with document counts."""
    return pipeline_db.get_all_hoas()


@app.get("/api/v1/hoas/{hoa_id}/documents")
async def list_hoa_documents(hoa_id: str):
    """Return all documents for a specific HOA, newest first."""
    return pipeline_db.get_documents_by_hoa(hoa_id)


@app.get("/health")
async def health():
    return {"status": "ok"}
