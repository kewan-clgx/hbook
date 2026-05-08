"""Stage 1 — Upload & Validation.

Accepts a PDF file, validates it, assigns a doc_id, and stores
the raw PDF under hoa-docs/raw/{hoa_id}/{doc_id}.pdf.
"""

import json
import uuid
import shutil
from pathlib import Path
from datetime import date
from typing import Optional

from pipeline.config import RAW_DIR, MAX_FILE_SIZE_MB, MAX_PAGE_COUNT, VALID_DOCUMENT_TYPES

_MANIFEST = ".manifest.json"


def _load_manifest(dest_dir: Path) -> dict:
    path = dest_dir / _MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_manifest(dest_dir: Path, manifest: dict) -> None:
    path = dest_dir / _MANIFEST
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


class ValidationError(Exception):
    """Raised when a document fails upload validation."""
    pass


def validate_pdf_magic_bytes(file_path: Path) -> None:
    """Check that the file starts with the PDF magic bytes."""
    with open(file_path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        raise ValidationError(f"File is not a valid PDF (magic bytes: {header!r})")


def validate_file_size(file_path: Path) -> None:
    """Reject files larger than MAX_FILE_SIZE_MB."""
    size_mb = file_path.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        raise ValidationError(
            f"File size {size_mb:.1f} MB exceeds limit of {MAX_FILE_SIZE_MB} MB"
        )


def validate_page_count(file_path: Path) -> int:
    """Return page count; raise if it exceeds MAX_PAGE_COUNT."""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(file_path))
        page_count = len(reader.pages)
    except Exception as e:
        raise ValidationError(f"Cannot read PDF pages: {e}")

    if page_count > MAX_PAGE_COUNT:
        raise ValidationError(
            f"Page count {page_count} exceeds limit of {MAX_PAGE_COUNT}"
        )
    return page_count


def validate_not_encrypted(file_path: Path) -> None:
    """Reject encrypted PDFs."""
    import pypdf
    reader = pypdf.PdfReader(str(file_path))
    if reader.is_encrypted:
        raise ValidationError("PDF is encrypted. Provide an unencrypted file or password.")


def validate_document_type(document_type: str) -> None:
    if document_type not in VALID_DOCUMENT_TYPES:
        raise ValidationError(
            f"Invalid document_type '{document_type}'. "
            f"Must be one of: {VALID_DOCUMENT_TYPES}"
        )


def validate_effective_date(document_type: str, effective_date: Optional[str]) -> None:
    """effective_date is required for amendments."""
    if document_type == "amendment" and not effective_date:
        raise ValidationError("effective_date is required for document_type 'amendment'")


def upload_and_validate(
    source_path: Path,
    hoa_id: str,
    document_type: str,
    effective_date: Optional[str] = None,
    supersedes_doc_id: Optional[str] = None,
) -> dict:
    """
    Validate and store a PDF document.

    Returns a metadata dict with doc_id and storage path.
    """
    validate_document_type(document_type)
    validate_effective_date(document_type, effective_date)

    dest_dir = RAW_DIR / hoa_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Return cached result if this filename was already uploaded
    manifest = _load_manifest(dest_dir)
    if source_path.name in manifest:
        cached = manifest[source_path.name]
        # Repair raw_path if it was written in a different environment (e.g. Docker vs local)
        expected_path = dest_dir / source_path.name
        if cached["raw_path"] != str(expected_path) and expected_path.exists():
            cached["raw_path"] = str(expected_path)
            manifest[source_path.name] = cached
            _save_manifest(dest_dir, manifest)
        return cached

    validate_pdf_magic_bytes(source_path)
    validate_file_size(source_path)
    validate_not_encrypted(source_path)
    page_count = validate_page_count(source_path)

    doc_id = str(uuid.uuid4())
    dest_path = dest_dir / source_path.name
    shutil.copy2(source_path, dest_path)

    result = {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "document_type": document_type,
        "effective_date": effective_date,
        "supersedes_doc_id": supersedes_doc_id,
        "page_count": page_count,
        "raw_path": str(dest_path),
        "status": "queued",
    }
    manifest[source_path.name] = result
    _save_manifest(dest_dir, manifest)
    return result
