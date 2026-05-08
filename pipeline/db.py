"""Postgres persistence — document registry and pipeline status tracking."""

import logging
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from pipeline.config import DATABASE_URL

logger = logging.getLogger("pipeline")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return _engine


def _exec(statement: str, params: dict) -> None:
    try:
        with _get_engine().connect() as conn:
            conn.execute(text(statement), params)
            conn.commit()
    except SQLAlchemyError as e:
        logger.warning(f'"db_write_failed", "error": "{e}"')


def upsert_hoa(hoa_id: str, hoa_name: str) -> None:
    _exec(
        """
        INSERT INTO hoas (hoa_id, name)
        VALUES (:hoa_id, :name)
        ON CONFLICT (hoa_id) DO UPDATE SET name = EXCLUDED.name
        """,
        {"hoa_id": hoa_id, "name": hoa_name},
    )


def insert_document(
    doc_id: str,
    hoa_id: str,
    original_filename: str,
    document_type: str,
    raw_path: str,
    page_count: int,
    effective_date: Optional[str] = None,
    supersedes_doc_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
) -> None:
    _exec(
        """
        INSERT INTO documents (
            doc_id, hoa_id, original_filename, document_type,
            raw_path, page_count, effective_date, supersedes_doc_id,
            uploaded_by, status
        ) VALUES (
            :doc_id, :hoa_id, :original_filename, :document_type,
            :raw_path, :page_count, :effective_date, :supersedes_doc_id,
            :uploaded_by, 'queued'
        )
        ON CONFLICT (doc_id) DO NOTHING
        """,
        {
            "doc_id": doc_id,
            "hoa_id": hoa_id,
            "original_filename": original_filename,
            "document_type": document_type,
            "raw_path": raw_path,
            "page_count": page_count,
            "effective_date": effective_date,
            "supersedes_doc_id": supersedes_doc_id,
            "uploaded_by": uploaded_by,
        },
    )


def update_document_status(doc_id: str, status: str) -> None:
    _exec(
        "UPDATE documents SET status = :status WHERE doc_id = :doc_id",
        {"doc_id": doc_id, "status": status},
    )


def get_all_hoas() -> list:
    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(text("""
                SELECT h.hoa_id, h.name,
                       COUNT(d.doc_id)   AS doc_count,
                       MAX(d.updated_at) AS last_updated
                FROM hoas h
                LEFT JOIN documents d ON h.hoa_id = d.hoa_id
                GROUP BY h.hoa_id, h.name
                ORDER BY h.name
            """)).fetchall()
            return [
                {
                    "hoa_id":       row.hoa_id,
                    "name":         row.name,
                    "doc_count":    row.doc_count,
                    "last_updated": row.last_updated.isoformat() if row.last_updated else None,
                }
                for row in rows
            ]
    except SQLAlchemyError as e:
        logger.warning(f'"db_read_failed", "error": "{e}"')
        return []


def get_documents_by_hoa(hoa_id: str) -> list:
    try:
        with _get_engine().connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT doc_id, hoa_id, original_filename, document_type,
                           status, page_count, effective_date, supersedes_doc_id,
                           raw_path, created_at, updated_at
                    FROM documents
                    WHERE hoa_id = :hoa_id
                    ORDER BY created_at DESC
                """),
                {"hoa_id": hoa_id},
            ).fetchall()
            return [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                 for k, v in row._mapping.items()}
                for row in rows
            ]
    except SQLAlchemyError as e:
        logger.warning(f'"db_read_failed", "error": "{e}"')
        return []


def get_document(doc_id: str) -> Optional[dict]:
    try:
        with _get_engine().connect() as conn:
            row = conn.execute(
                text("""
                    SELECT doc_id, hoa_id, original_filename, document_type,
                           status, page_count, effective_date, supersedes_doc_id,
                           raw_path, created_at, updated_at
                    FROM documents WHERE doc_id = :doc_id
                """),
                {"doc_id": doc_id},
            ).fetchone()
            if row:
                return {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for k, v in row._mapping.items()}
    except SQLAlchemyError as e:
        logger.warning(f'"db_read_failed", "error": "{e}"')
    return None
