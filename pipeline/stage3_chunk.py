"""Stage 3 — Section-Aware Chunking.

Takes parsed markdown and layout JSON, produces structured chunks
split by structural boundaries (Article → Section → Subsection).

Output: hoa-docs/chunks/{hoa_id}/{doc_id}_chunks.json
"""

import json
import re
from pathlib import Path
from typing import Optional

from pipeline.config import CHUNKS_DIR, CHUNK_TOKEN_LIMIT, CHUNK_OVERLAP_TOKENS


# ─── Heading patterns (per §6.4 algorithm) ───────────────────────────────────
ARTICLE_RE = re.compile(r"^#{1,2}\s*Article\s+([IVX]+|\d+)", re.IGNORECASE)
SECTION_MID_RE = re.compile(r"^#{1,3}\s*Section\s+(\d+(\.\d+)*)", re.IGNORECASE)
SUBSECTION_RE = re.compile(r"^#{1,4}\s*(\d+(\.\d+)+)")
DEFINITIONS_RE = re.compile(r"^#{1,3}\s*(Article\s+.*)?Definitions?$", re.IGNORECASE)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def split_at_paragraphs(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text at paragraph boundaries with overlap."""
    paragraphs = text.split("\n\n")
    chunks = []
    current = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)
        if current_tokens + para_tokens > max_tokens and current:
            chunks.append("\n\n".join(current))
            # Overlap: keep last paragraph(s) that fit within overlap budget
            overlap = []
            overlap_count = 0
            for p in reversed(current):
                t = estimate_tokens(p)
                if overlap_count + t > overlap_tokens:
                    break
                overlap.insert(0, p)
                overlap_count += t
            current = overlap
            current_tokens = overlap_count
        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def parse_heading(line: str) -> Optional[dict]:
    """Identify if a line is a section heading and extract its metadata."""
    if m := ARTICLE_RE.match(line):
        return {"depth": 1, "number": m.group(1), "text": line.lstrip("#").strip()}
    if m := SECTION_MID_RE.match(line):
        return {"depth": 2, "number": m.group(1), "text": line.lstrip("#").strip()}
    if m := SUBSECTION_RE.match(line):
        return {"depth": 3, "number": m.group(1), "text": line.lstrip("#").strip()}
    return None


def is_definitions_heading(line: str) -> bool:
    return bool(DEFINITIONS_RE.match(line))


def extract_defined_terms(text: str) -> list[str]:
    """Extract terms from a definitions section (bold or quoted terms)."""
    terms = []
    # Match patterns like **"Term"** or **Term** or "Term" means...
    for m in re.finditer(r'\*\*"?([^"*]+)"?\*\*|"([^"]+)"\s+(means?|shall mean|is defined)', text):
        term = m.group(1) or m.group(2)
        if term:
            terms.append(term.strip())
    return terms


def chunk_definitions_section(text: str, ancestor_chain: list[str]) -> list[dict]:
    """Split definitions section into one chunk per defined term."""
    chunks = []
    # Split by bold terms or numbered definitions
    parts = re.split(r'\n(?=\*\*"|(?:\d+\.\s+")|(?:[A-Z][a-z]+.*means))', text)

    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        terms = extract_defined_terms(part)
        chunks.append({
            "text": part,
            "chunk_type": "definition",
            "section": "Definitions",
            "ancestor_chain": ancestor_chain,
            "defined_terms": terms,
            "token_count": estimate_tokens(part),
        })
    return chunks


def chunk_markdown(md_path: Path, layout_path: Path, doc_id: str, hoa_id: str) -> dict:
    """
    Parse markdown into section-aware chunks.

    Returns the chunks JSON structure per §6.4.
    """
    output_path = CHUNKS_DIR / hoa_id / f"{doc_id}_chunks.json"
    if output_path.exists():
        cached = json.loads(output_path.read_text(encoding="utf-8"))
        if cached.get("pipeline_status") in ("chunked", "tagged"):
            return {
                "doc_id": doc_id,
                "hoa_id": hoa_id,
                "chunks_path": str(output_path),
                "chunk_count": cached.get("chunk_count", 0),
                "status": "chunked_cached",
            }

    md_text = md_path.read_text(encoding="utf-8")
    layout_data = json.loads(layout_path.read_text(encoding="utf-8"))

    lines = md_text.split("\n")
    chunks = []
    ancestor_chain = []
    current_section = ""
    current_content = []
    in_definitions = False
    current_heading_text = ""

    def flush_section():
        nonlocal current_content, in_definitions
        if not current_content:
            return

        text = "\n".join(current_content).strip()
        if not text:
            current_content = []
            return

        if in_definitions:
            def_chunks = chunk_definitions_section(text, list(ancestor_chain))
            for i, dc in enumerate(def_chunks):
                dc["chunk_id"] = f"{doc_id}::definitions::{len(chunks) + i}"
                chunks.append(dc)
        else:
            token_count = estimate_tokens(text)
            if token_count <= CHUNK_TOKEN_LIMIT:
                chunks.append({
                    "chunk_id": f"{doc_id}::{current_section}::{len(chunks)}",
                    "text": text,
                    "chunk_type": _infer_chunk_type(text),
                    "section": current_section,
                    "ancestor_chain": list(ancestor_chain),
                    "defined_terms": [],
                    "token_count": token_count,
                })
            else:
                # Split at paragraph boundaries with overlap
                split_texts = split_at_paragraphs(
                    text, CHUNK_TOKEN_LIMIT, CHUNK_OVERLAP_TOKENS
                )
                for sub_text in split_texts:
                    chunks.append({
                        "chunk_id": f"{doc_id}::{current_section}::{len(chunks)}",
                        "text": sub_text,
                        "chunk_type": _infer_chunk_type(sub_text),
                        "section": current_section,
                        "ancestor_chain": list(ancestor_chain),
                        "defined_terms": [],
                        "token_count": estimate_tokens(sub_text),
                    })

        current_content = []

    for line in lines:
        # Check if this is a heading
        if line.startswith("#"):
            heading = parse_heading(line)
            if heading:
                flush_section()
                # Update ancestor chain based on depth
                depth = heading["depth"]
                while len(ancestor_chain) >= depth:
                    ancestor_chain.pop()
                ancestor_chain.append(heading["text"])
                current_section = heading["number"]
                current_heading_text = heading["text"]
                in_definitions = is_definitions_heading(line)
                continue

        current_content.append(line)

    # Flush remaining content
    flush_section()

    # Attach bounding boxes from layout JSON
    _attach_bounding_boxes(chunks, layout_data)

    # Assign page numbers
    _attach_page_numbers(chunks, layout_data)

    # Build output
    output = {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "chunk_count": len(chunks),
        "pipeline_status": "chunked",
        "chunks": chunks,
    }

    # Write chunks JSON
    output_dir = CHUNKS_DIR / hoa_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{doc_id}_chunks.json"
    output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "chunks_path": str(output_path),
        "chunk_count": len(chunks),
        "status": "chunked",
    }


def _infer_chunk_type(text: str) -> str:
    """Infer chunk type from content."""
    # Table detection
    if "|" in text and text.count("|") > 4:
        token_count = estimate_tokens(text)
        if token_count > CHUNK_TOKEN_LIMIT:
            return "oversized_table"
        return "table"
    # Recital detection (preamble text, often starts with WHEREAS)
    if text.strip().upper().startswith("WHEREAS"):
        return "recital"
    return "rule"


def _attach_bounding_boxes(chunks: list[dict], layout_data: dict) -> None:
    """Best-effort: match chunks to bounding boxes from layout JSON."""
    if not layout_data.get("pages"):
        for chunk in chunks:
            chunk["bounding_box"] = [0, 0, 0, 0]
            chunk["page"] = 1
        return

    # Build ordered list of (value_snippet, bbox, page) from layout
    # Ordered so the first layout item that appears inside the chunk wins.
    layout_items: list[tuple[str, object, int]] = []
    text_bbox_map: dict[str, dict] = {}
    for page_data in layout_data.get("pages", []):
        page_num = page_data.get("page", 1)
        for item in page_data.get("items", []):
            value = item.get("value", "")
            if not value:
                continue
            bbox = item.get("bBox") or item.get("bbox") or [0, 0, 0, 0]
            snippet = value[:80]
            text_bbox_map[snippet] = {"bbox": bbox, "page": page_num}
            layout_items.append((value, bbox, page_num))

    for chunk in chunks:
        chunk_text = chunk["text"]

        # Strategy 1: prefix match — first 80 chars of chunk match a layout item value
        match = text_bbox_map.get(chunk_text[:80])
        if match:
            chunk["bounding_box"] = match["bbox"]
            chunk["page"] = match["page"]
            continue

        # Strategy 2: contains match — find the first layout item whose value
        # appears anywhere in the chunk text (handles markdown-wrapped text from Docling)
        found = False
        for value, bbox, page_num in layout_items:
            if len(value) >= 10 and value in chunk_text:
                chunk["bounding_box"] = bbox
                chunk["page"] = page_num
                found = True
                break

        if not found:
            chunk.setdefault("bounding_box", [0, 0, 0, 0])
            chunk.setdefault("page", 1)


def _attach_page_numbers(chunks: list[dict], layout_data: dict) -> None:
    """Ensure all chunks have a page number."""
    for chunk in chunks:
        if "page" not in chunk:
            chunk["page"] = 1
