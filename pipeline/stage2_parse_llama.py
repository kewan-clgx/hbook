"""Stage 2 — Parse with LlamaParse.

Takes a raw PDF and produces:
  - Markdown output: hoa-docs/parsed/{hoa_id}/{doc_id}.md
  - Layout JSON (bounding boxes): hoa-docs/parsed/{hoa_id}/{doc_id}_layout.json
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from pipeline.config import PARSED_DIR, LLAMA_CLOUD_API_KEY

logger = logging.getLogger("pipeline")


def _check_parse_cache(doc_id: str, hoa_id: str) -> dict | None:
    """Return cached stage-2 result if both output files exist and layout is non-empty."""
    md_path = PARSED_DIR / hoa_id / f"{doc_id}.md"
    layout_path = PARSED_DIR / hoa_id / f"{doc_id}_layout.json"
    if not (md_path.exists() and md_path.stat().st_size > 0 and layout_path.exists()):
        return None
    layout_data = json.loads(layout_path.read_text(encoding="utf-8"))
    # Treat empty pages as a cache miss UNLESS it's from the fallback parser
    # (fallback has no bbox by design; premium with empty pages should retry)
    if not layout_data.get("pages") and layout_data.get("source") != "fallback":
        logger.warning(
            '{"event": "parse_cache_miss_empty_layout", "doc_id": "%s", "hoa_id": "%s"}',
            doc_id, hoa_id,
        )
        return None
    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "md_path": str(md_path),
        "layout_path": str(layout_path),
        "parsed_pages": len(layout_data["pages"]),
        "needs_review": False,
        "status": "parsed_cached",
    }


def parse_document(
    raw_pdf_path: Path,
    doc_id: str,
    hoa_id: str,
    page_count: int,
) -> dict:
    """
    Parse a PDF using LlamaParse Premium mode.

    Returns metadata about the parsed outputs.
    """
    cached = _check_parse_cache(doc_id, hoa_id)
    if cached:
        return cached

    from llama_parse import LlamaParse

    if not LLAMA_CLOUD_API_KEY:
        raise RuntimeError("LLAMA_CLOUD_API_KEY environment variable is not set")

    # Configure parser per §6.3
    parser = LlamaParse(
        api_key=LLAMA_CLOUD_API_KEY,
        result_type="markdown",
        premium_mode=True,
        parsing_instruction=(
            "This is a homeowners association governance document "
            "(CC&R, bylaws, or amendment). Preserve all section numbers "
            "(e.g., 'Article IV', 'Section 4.2.1') as markdown headings. "
            "Preserve all tables, definitions, and numbered lists exactly."
        ),
        extract_charts=False,
        take_screenshot=True,
        annotate_links=False,
        language="en",
    )

    # Parse the document
    documents = parser.load_data(str(raw_pdf_path))

    # Combine all document pages into single markdown
    markdown_text = "\n\n".join(doc.text for doc in documents)

    if not markdown_text.strip():
        raise RuntimeError(
            f"LlamaParse returned empty content for {raw_pdf_path.name} "
            "(premium mode). Will retry with fallback."
        )

    # Get layout JSON with bounding boxes (via extra result)
    layout_json = _get_layout_json(parser, raw_pdf_path)

    # Ensure output directory exists
    output_dir = PARSED_DIR / hoa_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write markdown
    md_path = output_dir / f"{doc_id}.md"
    md_path.write_text(markdown_text, encoding="utf-8")

    # Write layout JSON
    layout_path = output_dir / f"{doc_id}_layout.json"
    layout_path.write_text(json.dumps(layout_json, indent=2), encoding="utf-8")

    # Verify page count consistency
    parsed_pages = len(layout_json.get("pages", []))
    needs_review = parsed_pages != page_count

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "md_path": str(md_path),
        "layout_path": str(layout_path),
        "parsed_pages": parsed_pages,
        "needs_review": needs_review,
        "status": "parsed",
    }


def _get_layout_json(parser: "LlamaParse", pdf_path: Path) -> dict:
    """
    Attempt to retrieve layout/bounding-box JSON from LlamaParse.

    Falls back to an empty structure if not available.
    """
    try:
        json_results = parser.get_json_result(str(pdf_path))
        if json_results and len(json_results) > 0:
            return json_results[0]
    except Exception as e:
        logger.warning(
            '{"event": "layout_json_failed", "pdf": "%s", "error": "%s"}',
            pdf_path.name, e,
        )

    return {"pages": []}


def parse_document_fallback(
    raw_pdf_path: Path,
    doc_id: str,
    hoa_id: str,
    page_count: int,
) -> dict:
    """
    Fallback parser: LlamaParse with premium_mode=False.
    Used when premium mode times out (>5 min).
    """
    cached = _check_parse_cache(doc_id, hoa_id)
    if cached:
        return cached

    from llama_parse import LlamaParse

    parser = LlamaParse(
        api_key=LLAMA_CLOUD_API_KEY,
        result_type="markdown",
        premium_mode=False,
        parsing_instruction=(
            "This is a homeowners association governance document. "
            "Preserve all section numbers as markdown headings. "
            "Preserve all tables and numbered lists exactly."
        ),
        language="en",
    )

    documents = parser.load_data(str(raw_pdf_path))
    markdown_text = "\n\n".join(doc.text for doc in documents)

    if not markdown_text.strip():
        raise RuntimeError(
            f"LlamaParse fallback also returned empty content for {raw_pdf_path.name}"
        )

    output_dir = PARSED_DIR / hoa_id
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{doc_id}.md"
    md_path.write_text(markdown_text, encoding="utf-8")

    # No bounding boxes in non-premium mode; source=fallback prevents cache miss loop
    layout_path = output_dir / f"{doc_id}_layout.json"
    layout_path.write_text(json.dumps({"pages": [], "source": "fallback"}, indent=2), encoding="utf-8")

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "md_path": str(md_path),
        "layout_path": str(layout_path),
        "parsed_pages": 0,
        "needs_review": True,
        "status": "parsed_fallback",
    }
