"""
pipeline_cleanup.py — Cache and index cleanup for the HOA ingestion pipeline.

Usage examples:

  # List all docs for a HOA (no --doc-id = list mode)
  python pipeline_cleanup.py --hoa-id woodbury-001

  # Full re-parse from stage 2 (parse + chunk + tag + index)
  python pipeline_cleanup.py --hoa-id woodbury-001 --doc-id <id>

  # Re-chunk + re-index (keep parsed markdown/layout)
  python pipeline_cleanup.py --hoa-id woodbury-001 --doc-id <id> --from-stage 3

  # Re-index only (keep parsed markdown, layout, and chunks)
  python pipeline_cleanup.py --hoa-id woodbury-001 --doc-id <id> --from-stage 5

  # Preview changes without deleting anything
  python pipeline_cleanup.py --hoa-id woodbury-001 --doc-id <id> --dry-run
"""

import argparse
import json
import pickle
import sys
from pathlib import Path


# ── Paths (mirrors pipeline/config.py) ────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent
PARSED_DIR    = PROJECT_ROOT / "hoa-docs" / "parsed"
CHUNKS_DIR    = PROJECT_ROOT / "hoa-docs" / "chunks"
CHROMA_DIR    = PROJECT_ROOT / "hoa-docs" / "chroma_db"
BM25_DIR      = PROJECT_ROOT / "hoa-docs" / "bm25_index"
RAW_DIR       = PROJECT_ROOT / "hoa-docs" / "raw"


def _load_manifest(hoa_id: str) -> dict:
    manifest_path = RAW_DIR / hoa_id / ".manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def list_docs(hoa_id: str) -> None:
    """Print all documents for a HOA with their pipeline state."""
    manifest = _load_manifest(hoa_id)
    if not manifest:
        print(f"No manifest found for hoa_id={hoa_id}")
        return

    print(f"\n{'─' * 80}")
    print(f"  HOA: {hoa_id}  ({len(manifest)} documents)")
    print(f"{'─' * 80}")
    print(f"  {'FILENAME':<40} {'DOC_ID':<10} {'STAGE':<10} {'PARSED':>6} {'CHUNKS':>6} {'INDEXED':>7}")
    print(f"  {'─'*40} {'─'*10} {'─'*10} {'─'*6} {'─'*6} {'─'*7}")

    for filename, entry in manifest.items():
        doc_id   = entry["doc_id"]
        doc_type = entry.get("document_type", "?")

        md_path     = PARSED_DIR / hoa_id / f"{doc_id}.md"
        layout_path = PARSED_DIR / hoa_id / f"{doc_id}_layout.json"
        chunks_path = CHUNKS_DIR / hoa_id / f"{doc_id}_chunks.json"
        sentinel    = CHROMA_DIR / ".indexed" / doc_id

        parsed  = "✓" if md_path.exists() else "✗"
        layout  = "✓" if layout_path.exists() else "✗"
        chunked = "✓" if chunks_path.exists() else "✗"
        indexed = "✓" if sentinel.exists() else "✗"

        # Chunk count
        chunk_count = ""
        if chunks_path.exists():
            try:
                data = json.loads(chunks_path.read_text(encoding="utf-8"))
                chunk_count = str(data.get("chunk_count", "?"))
            except Exception:
                chunk_count = "err"

        stage = f"md={parsed} lay={layout}"
        print(f"  {filename:<40} {doc_id[:8]}…  {doc_type:<10} {stage}  {chunked+chunk_count:>7} {indexed:>7}")

    print()

    # Chroma summary
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        coll_name = f"hoa_{hoa_id}"
        try:
            col = client.get_collection(coll_name)
            print(f"  Chroma '{coll_name}': {col.count()} total chunks")
        except Exception:
            print(f"  Chroma '{coll_name}': collection not found")
    except ImportError:
        pass

    # BM25 summary
    ids_path = BM25_DIR / hoa_id / "chunk_ids.json"
    if ids_path.exists():
        ids = json.loads(ids_path.read_text(encoding="utf-8"))
        print(f"  BM25  '{hoa_id}': {len(ids)} total chunks")

    print()


def cleanup_doc(hoa_id: str, doc_id: str, from_stage: int, dry_run: bool) -> None:
    """Delete cache files and index entries so the pipeline re-runs from from_stage."""
    verb = "Would delete" if dry_run else "Deleting"

    # ── Stage 5: Chroma + BM25 ────────────────────────────────────────────────
    if from_stage <= 5:
        sentinel = CHROMA_DIR / ".indexed" / doc_id
        if sentinel.exists():
            print(f"  {verb}: {sentinel}")
            if not dry_run:
                sentinel.unlink()

        # Purge Chroma entries for this doc
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(CHROMA_DIR))
            coll_name = f"hoa_{hoa_id}"
            try:
                col = client.get_collection(coll_name)
                before = col.count()
                if not dry_run:
                    col.delete(where={"doc_id": doc_id})
                    after = col.count()
                    print(f"  Chroma: {before} → {after} chunks (removed {before - after})")
                else:
                    print(f"  Would purge Chroma entries for doc_id={doc_id}")
            except Exception as e:
                print(f"  Chroma collection not found or empty: {e}")
        except ImportError:
            print("  chromadb not installed — skipping Chroma purge")

        # Purge BM25 entries for this doc
        ids_path = BM25_DIR / hoa_id / "chunk_ids.json"
        tok_path = BM25_DIR / hoa_id / "bm25_index.pkl"
        obj_path = BM25_DIR / hoa_id / "bm25_obj.pkl"
        if ids_path.exists() and tok_path.exists():
            existing_ids = json.loads(ids_path.read_text(encoding="utf-8"))
            prefix = f"{doc_id}::"
            removed = sum(1 for i in existing_ids if i.startswith(prefix))
            if dry_run:
                print(f"  Would purge {removed} BM25 entries for doc_id={doc_id}")
            else:
                existing_toks = pickle.loads(tok_path.read_bytes())
                kept = [(i, t) for i, t in zip(existing_ids, existing_toks)
                        if not i.startswith(prefix)]
                kept_ids   = [x[0] for x in kept]
                kept_toks  = [x[1] for x in kept]
                if kept_toks:
                    from rank_bm25 import BM25Okapi
                    bm25 = BM25Okapi(kept_toks)
                    tok_path.write_bytes(pickle.dumps(kept_toks))
                    ids_path.write_text(json.dumps(kept_ids))
                    obj_path.write_bytes(pickle.dumps(bm25))
                print(f"  BM25: removed {removed} entries, {len(kept_ids)} remaining")

    # ── Stage 3: Chunks ───────────────────────────────────────────────────────
    if from_stage <= 3:
        chunks_path = CHUNKS_DIR / hoa_id / f"{doc_id}_chunks.json"
        if chunks_path.exists():
            print(f"  {verb}: {chunks_path}")
            if not dry_run:
                chunks_path.unlink()

    # ── Stage 2: Parsed markdown + layout ─────────────────────────────────────
    if from_stage <= 2:
        for suffix in [".md", "_layout.json"]:
            p = PARSED_DIR / hoa_id / f"{doc_id}{suffix}"
            if p.exists():
                print(f"  {verb}: {p}")
                if not dry_run:
                    p.unlink()

    if dry_run:
        print("\n  (dry-run — nothing was deleted)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HOA pipeline cache cleanup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--hoa-id",     required=True,  help="HOA slug (e.g. woodbury-001)")
    parser.add_argument("--doc-id",                     help="Document UUID; omit to list docs")
    parser.add_argument("--from-stage", type=int, default=2, choices=[2, 3, 5],
                        help="Delete cache from this stage onward (2=parse, 3=chunk, 5=index)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print what would be deleted without deleting")
    args = parser.parse_args()

    if not args.doc_id:
        list_docs(args.hoa_id)
        return

    stage_names = {2: "parse → chunk → tag → index",
                   3: "chunk → tag → index",
                   5: "index only"}
    print(f"\nCleanup: hoa={args.hoa_id}  doc={args.doc_id}")
    print(f"Re-run from stage {args.from_stage} ({stage_names[args.from_stage]})")
    if args.dry_run:
        print("Mode: DRY RUN\n")
    print()

    cleanup_doc(args.hoa_id, args.doc_id, args.from_stage, args.dry_run)

    if not args.dry_run:
        print(f"\nDone. Now re-run the pipeline:")
        manifest = _load_manifest(args.hoa_id)
        filename = next((f for f, e in manifest.items() if e["doc_id"] == args.doc_id), "<filename>.pdf")
        doc_type = next((e.get("document_type", "bylaws") for f, e in manifest.items()
                        if e["doc_id"] == args.doc_id), "bylaws")
        print(f"""
  .venv/bin/python -m pipeline.cli \\
    "hoa-docs/raw/{args.hoa_id}/{filename}" \\
    --hoa-id   {args.hoa_id} \\
    --hoa-name "<HOA Display Name>" \\
    --document-type {doc_type}
""")


if __name__ == "__main__":
    main()
