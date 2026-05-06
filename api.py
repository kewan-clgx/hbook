"""FastAPI application — document ingestion API.

Endpoint: POST /api/v1/documents
"""

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pipeline.orchestrator import run_pipeline

app = FastAPI(title="HOA Compliance AI — Ingestion API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/v1/documents", status_code=202)
async def ingest_document(
    file: UploadFile = File(...),
    hoa_id: str = Form(...),
    hoa_name: str = Form(...),
    document_type: str = Form(...),
    effective_date: Optional[str] = Form(None),
    supersedes_doc_id: Optional[str] = Form(None),
    supersedes_doc_type: Optional[str] = Form(None),
):
    """Upload and ingest a governance PDF document."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    # Save upload to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        results = run_pipeline(
            source_pdf_path=str(tmp_path),
            hoa_id=hoa_id,
            hoa_name=hoa_name,
            document_type=document_type,
            effective_date=effective_date,
            supersedes_doc_id=supersedes_doc_id,
            supersedes_doc_type=supersedes_doc_type,
        )
        return results["summary"]
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/health")
async def health():
    return {"status": "ok"}
