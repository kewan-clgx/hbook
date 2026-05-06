"""Stage 4 — Metadata Tagging.

Enriches chunks with full metadata schema per §6.5.
Every chunk must carry the complete schema before indexing.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pipeline.config import AUTHORITY_RANK, CHUNKS_DIR


def tag_chunks(
    chunks_path: Path,
    doc_id: str,
    hoa_id: str,
    hoa_name: str,
    document_type: str,
    effective_date: Optional[str] = None,
    supersedes_doc_id: Optional[str] = None,
    supersedes_doc_type: Optional[str] = None,
) -> dict:
    """
    Enrich all chunks with full metadata.

    Returns updated chunks data and validation report.
    """
    chunks_data = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    chunks = chunks_data["chunks"]

    # Determine authority_rank
    authority_rank = _resolve_authority_rank(
        document_type, supersedes_doc_id, supersedes_doc_type
    )

    now = datetime.now(timezone.utc).isoformat()
    validation_errors = []

    for chunk in chunks:
        # Core identity
        chunk["doc_id"] = doc_id
        chunk["hoa_id"] = hoa_id
        chunk["hoa_name"] = hoa_name
        chunk["document_type"] = document_type
        chunk["authority_rank"] = authority_rank

        # Section hierarchy
        chunk.setdefault("section", "")
        chunk["parent_section"] = chunk["ancestor_chain"][0] if chunk.get("ancestor_chain") else ""

        # Dates and versioning
        chunk["effective_date"] = effective_date
        chunk["supersedes_doc_id"] = supersedes_doc_id
        chunk["last_updated"] = now

        # Extract referenced terms (simple NER: find capitalized multi-word phrases
        # or quoted terms that might reference definitions)
        chunk["referenced_terms"] = _extract_referenced_terms(chunk["text"])

        # Validate required fields
        errors = _validate_chunk(chunk)
        if errors:
            validation_errors.append({
                "chunk_id": chunk.get("chunk_id", "unknown"),
                "errors": errors,
            })

    # Write enriched chunks back
    chunks_data["chunks"] = chunks
    output_path = Path(chunks_path)
    output_path.write_text(json.dumps(chunks_data, indent=2), encoding="utf-8")

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "chunks_path": str(output_path),
        "chunk_count": len(chunks),
        "validation_errors": validation_errors,
        "status": "tagged" if not validation_errors else "tagged_with_errors",
    }


def _resolve_authority_rank(
    document_type: str,
    supersedes_doc_id: Optional[str],
    supersedes_doc_type: Optional[str],
) -> int:
    """Resolve authority rank, handling amendment inheritance."""
    rank = AUTHORITY_RANK.get(document_type)

    if rank is not None:
        return rank

    # Amendment: inherit from superseded document
    if document_type == "amendment":
        if supersedes_doc_type:
            inherited = AUTHORITY_RANK.get(supersedes_doc_type)
            if inherited is not None:
                return inherited
        # Default: amendments usually amend CC&Rs (rank 2)
        return 2

    # Fallback
    return 5


def _extract_referenced_terms(text: str) -> list[str]:
    """
    Extract terms that might reference definitions elsewhere.
    Looks for quoted terms and capitalized phrases that suggest defined terms.
    """
    terms = []

    # Quoted references: "Recreational Vehicle", "Common Area"
    for m in re.finditer(r'"([A-Z][^"]{2,40})"', text):
        terms.append(m.group(1))

    # Parenthetical definitions: (the "Association")
    for m in re.finditer(r'\((?:the\s+)?"([^"]+)"\)', text):
        terms.append(m.group(1))

    return list(set(terms))


def _validate_chunk(chunk: dict) -> list[str]:
    """Validate that all required fields are present and valid."""
    errors = []
    required_fields = [
        "chunk_id", "doc_id", "hoa_id", "hoa_name", "document_type",
        "authority_rank", "section", "parent_section", "ancestor_chain",
        "chunk_type", "last_updated", "page", "bounding_box",
        "token_count", "text",
    ]

    for field in required_fields:
        if field not in chunk or chunk[field] is None:
            # section can be empty string, that's okay
            if field == "section" and chunk.get(field) == "":
                continue
            errors.append(f"Missing required field: {field}")

    # Validate authority_rank range
    rank = chunk.get("authority_rank")
    if rank is not None and not (1 <= rank <= 5):
        errors.append(f"authority_rank {rank} not in range 1-5")

    # Validate chunk_type enum
    valid_types = {"rule", "definition", "table", "recital", "oversized_table"}
    if chunk.get("chunk_type") not in valid_types:
        errors.append(f"Invalid chunk_type: {chunk.get('chunk_type')}")

    return errors
