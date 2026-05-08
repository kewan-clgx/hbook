"""Stage 2 — Parse with Docling (local, no API credits required).

Takes a raw PDF and produces:
  - Markdown output: hoa-docs/parsed/{hoa_id}/{doc_id}.md
  - Layout JSON (bounding boxes): hoa-docs/parsed/{hoa_id}/{doc_id}_layout.json

Fallback: pypdf text extraction (no bounding boxes) when Docling fails.

Note: Docling downloads AI model weights on first run; cached in ~/.cache/docling.
"""

import json
import logging
import re
from pathlib import Path

from pipeline.config import PARSED_DIR

logger = logging.getLogger("pipeline")


# ─── Cache check (shared by both parsers) ────────────────────────────────────

def _check_parse_cache(doc_id: str, hoa_id: str) -> dict | None:
    """Return cached stage-2 result if both output files exist and layout is non-empty."""
    md_path = PARSED_DIR / hoa_id / f"{doc_id}.md"
    layout_path = PARSED_DIR / hoa_id / f"{doc_id}_layout.json"
    if not (md_path.exists() and md_path.stat().st_size > 0 and layout_path.exists()):
        return None
    layout_data = json.loads(layout_path.read_text(encoding="utf-8"))
    # Treat empty pages as a cache miss UNLESS it's from the fallback parser
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
        "parsed_pages": len(layout_data.get("pages", [])),
        "needs_review": False,
        "status": "parsed_cached",
    }


# ─── Docling converter factory ───────────────────────────────────────────────

def _make_converter():
    """Return a Docling DocumentConverter tuned for legal/governance PDFs."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel.base_models import InputFormat

    opts = PdfPipelineOptions()
    opts.do_table_structure = True   # accurate table cell detection
    opts.do_ocr = True               # handle scanned pages

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


# ─── Legal heading level detector ────────────────────────────────────────────

# Matches roman numerals I–XVIII (sufficient for HOA document articles)
_ROMAN = r"(?:I{1,3}V?|VI{0,3}|IX|X{1,3}|X[IV]|XVI{0,3}|XIX?)"

_ARTICLE_RE    = re.compile(rf"^(?:ARTICLE|Article)\s+(?:{_ROMAN}|\d+)\b", re.I)
_SECTION_RE    = re.compile(r"^(?:SECTION|Section)\s+(\d+)(\.(\d+))?(\.(\d+))?", re.I)
_SUBSEC_NUM_RE = re.compile(r"^(\d+)\.(\d+)(\.(\d+))?\s+\S")  # "4.2 Definitions"

# Detects inline section headers at the start of a text block:
#   "Section 3. Notice of Meetings. The board shall..."
#   Group 1 = section title ("Section 3. Notice of Meetings")
#   Group 2 = remaining body text
_INLINE_SECTION_RE = re.compile(
    r"^((?:Section|SECTION)\s+\d+(?:\.\d+)*\.?\s+[A-Z][^.]{0,80}\.)\s+(.+)",
    re.DOTALL | re.I,
)

# OCR artifacts like "ARTICLEN" (IV read as N) or "ARTICLEIV" (no space)
_ARTICLE_GARBLED_RE = re.compile(
    r"^ARTICLE([A-Z]+)\b",
    re.I,
)


def _normalize_article(text: str) -> str:
    """Fix common OCR artifacts in article headings (e.g. ARTICLEN → ARTICLE IV)."""
    m = _ARTICLE_GARBLED_RE.match(text.strip())
    if not m:
        return text
    suffix = m.group(1)
    # If suffix doesn't look like a valid roman/arabic number, tag it as unknown
    # so it still gets promoted to H2 rather than silently wrong
    if not re.match(rf"^(?:{_ROMAN}|\d+)$", suffix, re.I):
        return f"ARTICLE [OCR?:{suffix}]{text[len(m.group(0)):]}"
    return text  # already valid (e.g. ARTICLEI → caught by no-space check)


def _heading_level(text: str) -> int:
    """
    Map a Docling section_header text to a markdown heading level (2–4).

    Hierarchy for HOA governance documents:
      H2 → Article  (Article I, ARTICLE IV)
      H3 → Section  (Section 1.1, Section 4)
      H4 → Sub-section (Section 4.2.1, 4.2.1 Title)
    """
    t = text.strip()
    # Treat any ARTICLE... token as H2, even garbled ones
    if re.match(r"^ARTICLE\b", t, re.I):
        return 2
    m = _SECTION_RE.match(t)
    if m:
        return 4 if m.group(5) else 3
    if _SUBSEC_NUM_RE.match(t):
        return 4 if re.match(r"^\d+\.\d+\.\d+", t) else 3
    if re.match(r"^\d+\.\s+[A-Z]", t):
        return 3
    return 2  # default: H2 for unrecognised section_header


# ─── Custom markdown builder ──────────────────────────────────────────────────

def _build_markdown(result) -> str:
    """
    Build clean markdown from a Docling ConversionResult.

    Improvements over result.document.export_to_markdown():
      - Skips picture/figure items (no <!-- image --> noise)
      - Skips page headers and footers
      - Maps heading levels using Article/Section legal patterns
      - Preserves exact list markers (1., 2., a., b.) from the source PDF
      - Renders tables as markdown
    """
    from docling_core.types.doc import DocItemLabel

    SKIP_LABELS = {
        DocItemLabel.PICTURE,
        DocItemLabel.PAGE_HEADER,
        DocItemLabel.PAGE_FOOTER,
    }

    doc = result.document
    blocks: list[str] = []

    for item, level in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in SKIP_LABELS:
            continue

        text = (getattr(item, "text", "") or "").strip()

        if label == DocItemLabel.TITLE:
            if text:
                blocks.append(f"# {text}")

        elif label == DocItemLabel.SECTION_HEADER:
            if text:
                text = _normalize_article(text)
                h = _heading_level(text)
                blocks.append(f"{'#' * h} {text}")

        elif label == DocItemLabel.TEXT:
            if text:
                # Split "Section 3. Notice of Meetings. The board shall..."
                # into a proper H3 heading + body paragraph so that section
                # numbers are preserved in the heading hierarchy.
                m = _INLINE_SECTION_RE.match(text)
                if m:
                    section_title = m.group(1).rstrip(".")
                    body = m.group(2).strip()
                    h = _heading_level(section_title)
                    blocks.append(f"{'#' * h} {section_title}")
                    if body:
                        blocks.append(body)
                else:
                    blocks.append(text)

        elif label == DocItemLabel.LIST_ITEM:
            if text:
                # Use the exact marker from the PDF (1., a., -, •, etc.)
                marker = getattr(item, "marker", None) or "-"
                indent = "  " * max(0, level - 1)
                blocks.append(f"{indent}{marker} {text}")

        elif label == DocItemLabel.TABLE:
            try:
                blocks.append(item.export_to_markdown())
            except Exception:
                if text:
                    blocks.append(text)

        elif label == DocItemLabel.CAPTION:
            if text:
                blocks.append(f"*{text}*")

        elif label == DocItemLabel.FOOTNOTE:
            if text:
                blocks.append(f"> {text}")

        elif label == DocItemLabel.FORMULA:
            if text:
                blocks.append(f"`{text}`")

        elif label == DocItemLabel.CODE:
            if text:
                blocks.append(f"```\n{text}\n```")

        # All other labels (CHECKBOX, KEY_VALUE_REGION, etc.) → skip

    return "\n\n".join(blocks)


# ─── Layout JSON builder ─────────────────────────────────────────────────────

def _build_layout_json(result) -> dict:
    """
    Convert a Docling ConversionResult into the pipeline layout JSON format.

    Output shape (compatible with stage3_chunk._attach_bounding_boxes):
      {"pages": [{"page": 1, "items": [{"value": "...", "bBox": {x,y,w,h}}]}]}

    Docling BoundingBox uses (l, t, r, b) in PDF points, origin bottom-left
    (t > b). We store h = abs(t - b) so height is always positive.
    """
    pages_dict: dict[int, list[dict]] = {}

    try:
        for item, _ in result.document.iterate_items():
            if not getattr(item, "prov", None):
                continue
            for prov in item.prov:
                page_no = prov.page_no
                bbox = prov.bbox
                text = getattr(item, "text", None) or ""
                if not text.strip():
                    continue
                pages_dict.setdefault(page_no, []).append({
                    "value": text,
                    "bBox": {
                        "x": round(float(bbox.l), 2),
                        "y": round(float(bbox.t), 2),
                        "w": round(abs(float(bbox.r - bbox.l)), 2),
                        "h": round(abs(float(bbox.t - bbox.b)), 2),
                    },
                })
    except Exception as e:
        logger.warning('{"event": "layout_build_failed", "error": "%s"}', e)

    return {
        "pages": [
            {"page": p, "items": items}
            for p, items in sorted(pages_dict.items())
        ]
    }


# ─── Primary parser: Docling ─────────────────────────────────────────────────

def parse_document(
    raw_pdf_path: Path,
    doc_id: str,
    hoa_id: str,
    page_count: int,
) -> dict:
    """
    Parse a PDF using Docling (runs locally, no API credits).

    Docling performs layout analysis, table extraction, and markdown export.
    Model weights are downloaded once on first run and cached in ~/.cache/docling.
    """
    cached = _check_parse_cache(doc_id, hoa_id)
    if cached:
        return cached

    logger.info('{"event": "docling_start", "pdf": "%s"}', raw_pdf_path.name)

    converter = _make_converter()
    result = converter.convert(str(raw_pdf_path))

    markdown_text = _build_markdown(result)

    if not markdown_text.strip():
        raise RuntimeError(
            f"Docling returned empty content for {raw_pdf_path.name}. "
            "Will retry with fallback."
        )

    layout_json = _build_layout_json(result)

    output_dir = PARSED_DIR / hoa_id
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{doc_id}.md"
    md_path.write_text(markdown_text, encoding="utf-8")

    layout_path = output_dir / f"{doc_id}_layout.json"
    layout_path.write_text(json.dumps(layout_json, indent=2), encoding="utf-8")

    parsed_pages = len(layout_json["pages"])
    needs_review = parsed_pages != page_count

    logger.info(
        '{"event": "docling_complete", "pdf": "%s", "parsed_pages": %d, "needs_review": %s}',
        raw_pdf_path.name, parsed_pages, str(needs_review).lower(),
    )

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "md_path": str(md_path),
        "layout_path": str(layout_path),
        "parsed_pages": parsed_pages,
        "needs_review": needs_review,
        "status": "parsed",
    }


# ─── Fallback parser: pypdf ──────────────────────────────────────────────────

def parse_document_fallback(
    raw_pdf_path: Path,
    doc_id: str,
    hoa_id: str,
    page_count: int,
) -> dict:
    """
    Fallback parser using pypdf (pure text extraction, no layout analysis).
    Used when Docling fails (corrupt PDF, unsupported format, etc.).
    Produces no bounding-box data; chunks will have page=1 and bbox=[0,0,0,0].
    """
    cached = _check_parse_cache(doc_id, hoa_id)
    if cached:
        return cached

    import pypdf

    logger.warning(
        '{"event": "pypdf_fallback_start", "pdf": "%s"}', raw_pdf_path.name
    )

    reader = pypdf.PdfReader(str(raw_pdf_path))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    markdown_text = "\n\n---\n\n".join(pages_text)

    if not markdown_text.strip():
        raise RuntimeError(
            f"pypdf fallback also returned empty content for {raw_pdf_path.name}"
        )

    output_dir = PARSED_DIR / hoa_id
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{doc_id}.md"
    md_path.write_text(markdown_text, encoding="utf-8")

    # source=fallback prevents cache-miss loop (no bboxes by design)
    layout_path = output_dir / f"{doc_id}_layout.json"
    layout_path.write_text(
        json.dumps({"pages": [], "source": "fallback"}, indent=2),
        encoding="utf-8",
    )

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "md_path": str(md_path),
        "layout_path": str(layout_path),
        "parsed_pages": 0,
        "needs_review": True,
        "status": "parsed_fallback",
    }
