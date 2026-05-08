"""
QA script: verify a document is correctly indexed in Chroma and BM25.

Usage:
  python qa_index.py --hoa-id woodbury-001 --doc-id 553a2cdf-... --query "parking violations"
  python qa_index.py --hoa-id woodbury-001          # summary for whole HOA
"""

import argparse
import json
import pickle
import sys
from pathlib import Path


def chroma_summary(hoa_id: str, doc_id: str | None) -> None:
    import chromadb
    from pipeline.config import CHROMA_PERSIST_DIR

    client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))
    collections = [c.name for c in client.list_collections()]
    col_name = f"hoa_{hoa_id}"

    print(f"[Chroma] collections present: {collections}")

    if col_name not in collections:
        print(f"[Chroma] ERROR: collection '{col_name}' not found")
        return

    col = client.get_collection(col_name)
    total = col.count()
    print(f"[Chroma] '{col_name}' total chunks: {total}")

    if doc_id:
        result = col.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
        n = len(result["ids"])
        print(f"[Chroma] chunks for doc_id={doc_id}: {n}")
        if n == 0:
            print("[Chroma] ERROR: no chunks found for this doc_id")
            return

        # Page coverage
        pages = sorted(set(m["page"] for m in result["metadatas"]))
        types = {}
        for m in result["metadatas"]:
            types[m["chunk_type"]] = types.get(m["chunk_type"], 0) + 1

        print(f"[Chroma] pages represented: {pages}")
        print(f"[Chroma] chunk types: {types}")
        print(f"[Chroma] authority_rank: {result['metadatas'][0]['authority_rank']}")
        print(f"[Chroma] document_type: {result['metadatas'][0]['document_type']}")
        print()
        print(f"[Chroma] sample chunks:")
        for i in range(min(3, n)):
            meta = result["metadatas"][i]
            text = result["documents"][i][:100].replace("\n", " ")
            print(f"  [{i+1}] id={result['ids'][i]}")
            print(f"       page={meta['page']}  section={meta['section']!r}  type={meta['chunk_type']}")
            print(f"       text: {text!r}")


def bm25_summary(hoa_id: str, doc_id: str | None) -> None:
    from pipeline.config import BM25_INDEX_DIR

    idx_dir = BM25_INDEX_DIR / hoa_id
    ids_path = idx_dir / "chunk_ids.json"
    obj_path = idx_dir / "bm25_obj.pkl"

    if not ids_path.exists() or not obj_path.exists():
        print(f"[BM25]  ERROR: index files missing at {idx_dir}")
        return

    chunk_ids = json.loads(ids_path.read_text())
    print(f"[BM25]  corpus size: {len(chunk_ids)} chunk IDs")

    if doc_id:
        doc_chunks = [cid for cid in chunk_ids if cid.startswith(doc_id)]
        print(f"[BM25]  chunks for doc_id={doc_id}: {len(doc_chunks)}")
        if not doc_chunks:
            print("[BM25]  ERROR: no chunks found for this doc_id")


def hybrid_query(query: str, hoa_id: str, top_k: int = 5) -> None:
    from pipeline.stage5_index import hybrid_search

    print(f"\n[Query] '{query}'  (top {top_k}, hybrid RRF)")
    results = hybrid_search(query, hoa_id, top_k=top_k)

    if not results:
        print("[Query] ERROR: no results returned")
        return

    for i, r in enumerate(results):
        meta = r["metadata"]
        text = r["text"][:120].replace("\n", " ")
        print(f"  [{i+1}] doc={meta['doc_id'][:8]}…  page={meta['page']}"
              f"  type={meta['document_type']}  section={meta['section']!r}")
        print(f"       {text!r}")


def main() -> None:
    ap = argparse.ArgumentParser(description="QA: verify Chroma + BM25 index")
    ap.add_argument("--hoa-id",  required=True)
    ap.add_argument("--doc-id",  default=None, help="Narrow checks to one document")
    ap.add_argument("--query",   default=None, help="Run a hybrid search query")
    ap.add_argument("--top-k",   type=int, default=5)
    args = ap.parse_args()

    print("=" * 60)
    chroma_summary(args.hoa_id, args.doc_id)
    print()
    bm25_summary(args.hoa_id, args.doc_id)

    if args.query:
        hybrid_query(args.query, args.hoa_id, args.top_k)

    print("=" * 60)


if __name__ == "__main__":
    main()
