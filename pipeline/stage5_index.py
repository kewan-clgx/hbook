"""Stage 5 — Hybrid Indexing (Vector + BM25).

Indexes chunks into:
  - Chroma (vector search via Google Gemini embeddings)
  - BM25 (keyword search via rank_bm25)

Both indexes must succeed for a chunk to be considered indexed.
"""

import json
import pickle
import re
from pathlib import Path
from typing import Optional

from pipeline.config import (
    CHROMA_PERSIST_DIR,
    BM25_INDEX_DIR,
    GEMINI_API_KEY,
    EMBEDDING_MODEL,
)


def index_chunks(chunks_path: Path, doc_id: str, hoa_id: str) -> dict:
    """
    Index all chunks into both Chroma (vector) and BM25 (keyword).

    Both must succeed or the batch is rejected.
    """
    chunks_data = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
    chunks = chunks_data["chunks"]

    if not chunks:
        return {
            "doc_id": doc_id,
            "hoa_id": hoa_id,
            "indexed_count": 0,
            "status": "indexed_empty",
        }

    # Index A: Chroma vector index
    _index_chroma(chunks, hoa_id)

    # Index B: BM25 keyword index
    _index_bm25(chunks, hoa_id)

    return {
        "doc_id": doc_id,
        "hoa_id": hoa_id,
        "indexed_count": len(chunks),
        "status": "indexed",
    }


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via Google Gemini."""
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=texts,
        task_type="RETRIEVAL_DOCUMENT",
    )
    return result["embedding"]


def _embed_query(text: str) -> list[float]:
    """Generate a single query embedding via Google Gemini."""
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type="RETRIEVAL_QUERY",
    )
    return result["embedding"]


def _index_chroma(chunks: list[dict], hoa_id: str) -> None:
    """Index chunks into Chroma vector store."""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
    collection = client.get_or_create_collection(
        name=f"hoa_{hoa_id}",
        metadata={"hnsw:space": "cosine"},
    )

    # Batch embeddings
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        texts = [c["text"] for c in batch]

        embeddings = _embed_texts(texts)

        collection.upsert(
            ids=[c["chunk_id"] for c in batch],
            documents=texts,
            embeddings=embeddings,
            metadatas=[
                {
                    "doc_id": c["doc_id"],
                    "document_type": c["document_type"],
                    "authority_rank": c["authority_rank"],
                    "section": c["section"],
                    "parent_section": c["parent_section"],
                    "chunk_type": c["chunk_type"],
                    "effective_date": c.get("effective_date") or "",
                    "page": c["page"],
                }
                for c in batch
            ],
        )


def _index_bm25(chunks: list[dict], hoa_id: str) -> None:
    """Index chunks into a BM25 keyword index."""
    from rank_bm25 import BM25Okapi

    # Tokenize
    tokenized = [_tokenize(c["text"]) for c in chunks]
    chunk_ids = [c["chunk_id"] for c in chunks]

    # Build BM25 index
    bm25 = BM25Okapi(tokenized)

    # Load existing index if present, merge
    index_dir = BM25_INDEX_DIR / hoa_id
    index_dir.mkdir(parents=True, exist_ok=True)

    index_path = index_dir / "bm25_index.pkl"
    ids_path = index_dir / "chunk_ids.json"

    # For simplicity in local dev: rebuild full index per HOA
    # (production would do incremental updates)
    existing_ids = []
    existing_tokens = []

    if ids_path.exists() and index_path.exists():
        existing_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        existing_tokens = pickle.loads(index_path.read_bytes())

    # Merge: remove any existing chunks with same IDs (upsert behavior)
    new_id_set = set(chunk_ids)
    merged_ids = []
    merged_tokens = []

    for eid, etok in zip(existing_ids, existing_tokens):
        if eid not in new_id_set:
            merged_ids.append(eid)
            merged_tokens.append(etok)

    merged_ids.extend(chunk_ids)
    merged_tokens.extend(tokenized)

    # Rebuild BM25 with merged data
    if merged_tokens:
        bm25 = BM25Okapi(merged_tokens)
        index_path.write_bytes(pickle.dumps(merged_tokens))
        ids_path.write_text(json.dumps(merged_ids), encoding="utf-8")

        # Also save the BM25 object for query-time use
        bm25_obj_path = index_dir / "bm25_obj.pkl"
        bm25_obj_path.write_bytes(pickle.dumps(bm25))


def _tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer for BM25."""
    text = text.lower()
    # Keep alphanumeric, dots (for section numbers), and hyphens
    tokens = re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)*(?:-[a-z0-9]+)*", text)
    return tokens


# ─── Query-time retrieval ─────────────────────────────────────────────────────

def hybrid_search(
    query: str,
    hoa_id: str,
    top_k: int = 10,
    authority_rank_filter: Optional[int] = None,
    rrf_k: int = 60,
) -> list[dict]:
    """
    Hybrid retrieval using Reciprocal Rank Fusion (RRF).

    score(chunk) = 1/(k + vector_rank) + 1/(k + bm25_rank)
    """
    vector_results = _search_chroma(query, hoa_id, top_k * 2, authority_rank_filter)
    bm25_results = _search_bm25(query, hoa_id, top_k * 2)

    # RRF merge
    scores = {}
    for rank, chunk_id in enumerate(vector_results):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (rrf_k + rank + 1)
    for rank, chunk_id in enumerate(bm25_results):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (rrf_k + rank + 1)

    # Sort by combined score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    # Fetch full chunk data from Chroma
    return _fetch_chunks_by_ids([cid for cid, _ in ranked], hoa_id)


def _search_chroma(
    query: str, hoa_id: str, n_results: int, authority_rank_filter: Optional[int]
) -> list[str]:
    """Vector search via Chroma."""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))

    try:
        collection = client.get_collection(name=f"hoa_{hoa_id}")
    except Exception:
        return []

    # Embed query via Gemini
    query_embedding = _embed_query(query)

    # Build where filter
    where_filter = None
    if authority_rank_filter is not None:
        where_filter = {"authority_rank": {"$lte": authority_rank_filter}}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where_filter,
    )

    return results["ids"][0] if results["ids"] else []


def _search_bm25(query: str, hoa_id: str, top_k: int) -> list[str]:
    """BM25 keyword search."""
    index_dir = BM25_INDEX_DIR / hoa_id
    ids_path = index_dir / "chunk_ids.json"
    bm25_obj_path = index_dir / "bm25_obj.pkl"

    if not ids_path.exists() or not bm25_obj_path.exists():
        return []

    chunk_ids = json.loads(ids_path.read_text(encoding="utf-8"))
    bm25 = pickle.loads(bm25_obj_path.read_bytes())

    tokenized_query = _tokenize(query)
    scores = bm25.get_scores(tokenized_query)

    # Get top-k indices
    ranked_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [chunk_ids[i] for i in ranked_indices[:top_k]]


def _fetch_chunks_by_ids(chunk_ids: list[str], hoa_id: str) -> list[dict]:
    """Fetch full chunk documents from Chroma by ID."""
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))

    try:
        collection = client.get_collection(name=f"hoa_{hoa_id}")
    except Exception:
        return []

    if not chunk_ids:
        return []

    results = collection.get(ids=chunk_ids, include=["documents", "metadatas"])

    output = []
    for i, chunk_id in enumerate(results["ids"]):
        output.append({
            "chunk_id": chunk_id,
            "text": results["documents"][i],
            "metadata": results["metadatas"][i],
        })
    return output
