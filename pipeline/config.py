"""Pipeline configuration — local dev settings."""

import os
from pathlib import Path

# ─── Base paths ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOA_DOCS_ROOT = PROJECT_ROOT / "hoa-docs"

# Local storage directories (mirrors S3 layout in prod)
RAW_DIR = HOA_DOCS_ROOT / "raw"          # {hoa_id}/{doc_id}.pdf
PARSED_DIR = HOA_DOCS_ROOT / "parsed"    # {hoa_id}/{doc_id}.md + {doc_id}_layout.json
CHUNKS_DIR = HOA_DOCS_ROOT / "chunks"    # {hoa_id}/{doc_id}_chunks.json

# ─── LlamaParse ───────────────────────────────────────────────────────────────
LLAMA_CLOUD_API_KEY = os.environ.get("LLAMA_CLOUD_API_KEY", "")

# ─── Google Gemini ────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
EMBEDDING_MODEL = "models/text-embedding-004"

# ─── Chroma ───────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR = HOA_DOCS_ROOT / "chroma_db"

# ─── BM25 ─────────────────────────────────────────────────────────────────────
BM25_INDEX_DIR = HOA_DOCS_ROOT / "bm25_index"

# ─── Postgres (local dev) ─────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/hoa_compliance"
)

# ─── Pipeline constraints ─────────────────────────────────────────────────────
MAX_FILE_SIZE_MB = 100
MAX_PAGE_COUNT = 500
CHUNK_TOKEN_LIMIT = 1000
CHUNK_OVERLAP_TOKENS = 100
CONFIDENCE_THRESHOLD = 0.85

# ─── Document types & authority ranks ─────────────────────────────────────────
VALID_DOCUMENT_TYPES = [
    "state_statute", "ccr", "articles", "bylaws", "rules", "amendment"
]

AUTHORITY_RANK = {
    "state_statute": 1,
    "ccr": 2,
    "articles": 3,
    "bylaws": 4,
    "rules": 5,
    "amendment": None,  # inherits from supersedes_doc_id
}

# ─── Ensure directories exist ─────────────────────────────────────────────────
for d in [RAW_DIR, PARSED_DIR, CHUNKS_DIR, CHROMA_PERSIST_DIR, BM25_INDEX_DIR]:
    d.mkdir(parents=True, exist_ok=True)
