"""Pipeline Orchestrator — runs all 5 stages sequentially.

Each stage is idempotent and writes to durable local storage,
so failures are recoverable without reprocessing previous stages.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.config import RAW_DIR, PARSED_DIR, CHUNKS_DIR
from pipeline.stage1_upload import upload_and_validate
from pipeline.stage2_parse import parse_document, parse_document_fallback
from pipeline.stage3_chunk import chunk_markdown
from pipeline.stage4_tag import tag_chunks
from pipeline.stage5_index import index_chunks

logger = logging.getLogger("pipeline")
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":%(message)s}',
)


def run_pipeline(
    source_pdf_path: str,
    hoa_id: str,
    hoa_name: str,
    document_type: str,
    effective_date: Optional[str] = None,
    supersedes_doc_id: Optional[str] = None,
    supersedes_doc_type: Optional[str] = None,
) -> dict:
    """
    Run the full 5-stage ingestion pipeline on a single PDF.

    Returns a summary of all stage results.
    """
    pdf_path = Path(source_pdf_path)
    results = {}
    pipeline_start = time.time()

    # ─── Stage 1: Upload & Validate ──────────────────────────────────────────
    logger.info(json.dumps({
        "event": "stage_start", "stage": "upload", "source": str(pdf_path)
    }))
    stage_start = time.time()

    stage1 = upload_and_validate(
        source_path=pdf_path,
        hoa_id=hoa_id,
        document_type=document_type,
        effective_date=effective_date,
        supersedes_doc_id=supersedes_doc_id,
    )
    results["stage1_upload"] = stage1
    doc_id = stage1["doc_id"]

    logger.info(json.dumps({
        "event": "stage_complete", "stage": "upload",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "duration_ms": int((time.time() - stage_start) * 1000),
        "page_count": stage1["page_count"],
    }))

    # ─── Stage 2: Parse ──────────────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "stage_start", "stage": "parse", "doc_id": doc_id
    }))
    stage_start = time.time()

    raw_path = Path(stage1["raw_path"])
    try:
        stage2 = parse_document(
            raw_pdf_path=raw_path,
            doc_id=doc_id,
            hoa_id=hoa_id,
            page_count=stage1["page_count"],
        )
    except Exception as e:
        logger.warning(json.dumps({
            "event": "stage_retry", "stage": "parse",
            "doc_id": doc_id, "error": str(e),
        }))
        # Retry with fallback (non-premium)
        stage2 = parse_document_fallback(
            raw_pdf_path=raw_path,
            doc_id=doc_id,
            hoa_id=hoa_id,
            page_count=stage1["page_count"],
        )

    results["stage2_parse"] = stage2

    logger.info(json.dumps({
        "event": "stage_complete", "stage": "parse",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "duration_ms": int((time.time() - stage_start) * 1000),
        "parsed_pages": stage2.get("parsed_pages"),
    }))

    # ─── Stage 3: Chunk ──────────────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "stage_start", "stage": "chunk", "doc_id": doc_id
    }))
    stage_start = time.time()

    md_path = Path(stage2["md_path"])
    layout_path = Path(stage2["layout_path"])

    stage3 = chunk_markdown(
        md_path=md_path,
        layout_path=layout_path,
        doc_id=doc_id,
        hoa_id=hoa_id,
    )
    results["stage3_chunk"] = stage3

    logger.info(json.dumps({
        "event": "stage_complete", "stage": "chunk",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "duration_ms": int((time.time() - stage_start) * 1000),
        "chunk_count": stage3["chunk_count"],
    }))

    # ─── Stage 4: Tag Metadata ───────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "stage_start", "stage": "tag", "doc_id": doc_id
    }))
    stage_start = time.time()

    stage4 = tag_chunks(
        chunks_path=Path(stage3["chunks_path"]),
        doc_id=doc_id,
        hoa_id=hoa_id,
        hoa_name=hoa_name,
        document_type=document_type,
        effective_date=effective_date,
        supersedes_doc_id=supersedes_doc_id,
        supersedes_doc_type=supersedes_doc_type,
    )
    results["stage4_tag"] = stage4

    logger.info(json.dumps({
        "event": "stage_complete", "stage": "tag",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "duration_ms": int((time.time() - stage_start) * 1000),
        "chunk_count": stage4["chunk_count"],
        "validation_errors": len(stage4.get("validation_errors", [])),
    }))

    # ─── Stage 5: Index ──────────────────────────────────────────────────────
    logger.info(json.dumps({
        "event": "stage_start", "stage": "index", "doc_id": doc_id
    }))
    stage_start = time.time()

    stage5 = index_chunks(
        chunks_path=Path(stage4["chunks_path"]),
        doc_id=doc_id,
        hoa_id=hoa_id,
    )
    results["stage5_index"] = stage5

    logger.info(json.dumps({
        "event": "stage_complete", "stage": "index",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "duration_ms": int((time.time() - stage_start) * 1000),
        "indexed_count": stage5["indexed_count"],
    }))

    # ─── Summary ─────────────────────────────────────────────────────────────
    total_duration_ms = int((time.time() - pipeline_start) * 1000)
    results["summary"] = {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "document_type": document_type,
        "total_duration_ms": total_duration_ms,
        "final_status": stage5["status"],
        "chunk_count": stage5["indexed_count"],
    }

    logger.info(json.dumps({
        "event": "pipeline_complete",
        "doc_id": doc_id, "hoa_id": hoa_id,
        "total_duration_ms": total_duration_ms,
        "final_status": stage5["status"],
    }))

    return results
