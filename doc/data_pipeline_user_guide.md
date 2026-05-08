# Data Pipeline — Test, QA & Operations Guide

**Audience:** Developers and QA engineers working on the HOA Compliance AI ingestion pipeline.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites & Setup](#2-prerequisites--setup)
3. [Running the Pipeline](#3-running-the-pipeline)
4. [QA Checks — Per Stage](#4-qa-checks--per-stage)
5. [End-to-End QA with qa_index.py](#5-end-to-end-qa-with-qa_indexpy)
6. [Parser Output Quality](#6-parser-output-quality)
7. [Cache Cleanup & Re-processing](#7-cache-cleanup--re-processing)
8. [Inspecting the Database](#8-inspecting-the-database)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Architecture Overview

The pipeline runs in five sequential stages. Each stage is idempotent — it checks for existing output before doing work, so a re-run picks up where it left off.

```
Stage 1  Upload & Validate
         Validates PDF (magic bytes, size, encryption, page count).
         Copies to hoa-docs/raw/{hoa_id}/{filename}.
         Writes .manifest.json in the raw directory.
         → DB: INSERT INTO documents (status = 'queued')

Stage 2  Parse  (Docling — local, no API credits)
         Runs layout analysis, table extraction, OCR.
         Writes: hoa-docs/parsed/{hoa_id}/{doc_id}.md
                 hoa-docs/parsed/{hoa_id}/{doc_id}_layout.json   ← bounding boxes per page
         Fallback: pypdf text extraction if Docling fails.
         → DB: UPDATE documents SET status = 'parsed'

Stage 3  Chunk
         Splits markdown into semantic chunks (Article → Section → Subsection).
         Attaches page numbers and bounding boxes from layout JSON.
         Writes: hoa-docs/chunks/{hoa_id}/{doc_id}_chunks.json

Stage 4  Tag
         Stamps each chunk with HOA metadata (hoa_id, document_type,
         authority_rank, effective_date, supersedes_doc_id).
         Updates the same chunks file (pipeline_status → "tagged").
         → DB: UPDATE documents SET status = 'tagged'

Stage 5  Index  (Vector + BM25)
         Embeds chunks via Gemini (models/gemini-embedding-001).
         Purges any existing chunks for this doc_id before inserting.
         Writes to Chroma collection: hoa_{hoa_id}
         Merges into BM25 index:      hoa-docs/bm25_index/{hoa_id}/
         Writes sentinel:             hoa-docs/chroma_db/.indexed/{doc_id}
         → DB: UPDATE documents SET status = 'indexed'
```

**Authority ranks** (lower = higher authority):

| document_type  | authority_rank |
|----------------|---------------|
| state_statute  | 1             |
| ccr            | 2             |
| articles       | 3             |
| bylaws         | 4             |
| rules          | 5             |
| amendment      | inherits      |

---

## 2. Prerequisites & Setup

### Environment

```bash
# .env (project root) — required keys:
GEMINI_API_KEY=AIza...
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5433/hoa_compliance
```

> Note: `LLAMA_CLOUD_API_KEY` is no longer required. The pipeline uses Docling (local, no API credits).

### Services

```bash
# Start Postgres and backend (from project root)
docker compose up -d postgres backend frontend

# Verify all healthy
docker compose ps
```

All three services should show `Up`:

```
hbook-backend-1    Up   0.0.0.0:8000->8000/tcp
hbook-frontend-1   Up   0.0.0.0:3000->3000/tcp
hbook-postgres-1   Up   0.0.0.0:5433->5432/tcp
```

### Local virtual environment (for CLI and QA tools)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Docling downloads AI model weights (~500 MB) on first run; cached in `~/.cache/docling` and `.venv/`. Subsequent runs are local-only.

---

## 3. Running the Pipeline

### Option A — Browser UI (full API path)

1. Open `http://localhost:3000`
2. Choose a PDF, enter HOA ID, HOA Name, Document Type
3. Click **Upload & Ingest**
4. Watch the per-stage progress tracker update live (polls every 2 s)
5. Final state: all four stages show green checkmarks

### Option B — CLI (local venv, bypasses API)

```bash
source .venv/bin/activate

.venv/bin/python -m pipeline.cli \
  "hoa-docs/raw/woodbury-001/Woodbury Bylaws.pdf" \
  --hoa-id   woodbury-001 \
  --hoa-name "Woodbury Community" \
  --document-type bylaws \
  2>&1 | tee logs/myrun.log
```

For an amendment (requires `--effective-date` and `--supersedes-doc-id`):

```bash
.venv/bin/python -m pipeline.cli \
  "path/to/Amendment.pdf" \
  --hoa-id          woodbury-001 \
  --hoa-name        "Woodbury Community" \
  --document-type   amendment \
  --effective-date  2024-01-01 \
  --supersedes-doc-id  553a2cdf-3419-44ae-bb53-3888b685f10b \
  --supersedes-doc-type bylaws \
  2>&1 | tee logs/amendment-run.log
```

### Option C — REST API (curl)

```bash
curl -X POST http://localhost:8000/api/v1/documents \
  -F "file=@/path/to/Document.pdf" \
  -F "hoa_id=woodbury-001" \
  -F "hoa_name=Woodbury Community" \
  -F "document_type=bylaws"
# → {"doc_id": "...", "status": "queued"}

# Poll status
curl http://localhost:8000/api/v1/documents/<doc_id>
```

Valid `document_type` values: `state_statute`, `ccr`, `articles`, `bylaws`, `rules`, `amendment`

---

## 4. QA Checks — Per Stage

### Stage 1 — Upload & Validate

**Artifacts to check:**

```bash
# Raw PDF copied with original filename preserved
ls hoa-docs/raw/<hoa_id>/

# Manifest records doc_id and raw_path
cat hoa-docs/raw/<hoa_id>/.manifest.json
```

**Expected manifest shape:**

```json
{
  "Woodbury Bylaws.pdf": {
    "doc_id": "553a2cdf-3419-44ae-bb53-3888b685f10b",
    "hoa_id": "woodbury-001",
    "document_type": "bylaws",
    "page_count": 27,
    "raw_path": "/Users/.../hoa-docs/raw/woodbury-001/Woodbury Bylaws.pdf",
    "status": "queued"
  }
}
```

**DB check:**

```sql
SELECT doc_id, original_filename, status, page_count
FROM documents
WHERE hoa_id = 'woodbury-001';
```

---

### Stage 2 — Parse (Docling)

**Artifacts to check:**

```bash
# Markdown output (should be non-empty, > 1 KB per page)
wc -c hoa-docs/parsed/<hoa_id>/<doc_id>.md

# Layout JSON — should have pages array matching page count
python3 -c "
import json
layout = json.loads(open('hoa-docs/parsed/<hoa_id>/<doc_id>_layout.json').read())
print('pages:', len(layout['pages']))
print('source:', layout.get('source', 'docling'))  # 'fallback' = pypdf was used
"
```

**What to look for:**

| Indicator | Healthy | Problem |
|-----------|---------|---------|
| `.md` size | > 1 KB per page | Near-zero → parser got empty content |
| `pages` count | Matches `page_count` in manifest | 0 → layout extraction failed |
| `source` field | absent (Docling) | `"fallback"` → pypdf was used (no bbox data) |
| Heading structure | H2 for Articles, H3 for Sections | All H2 → inline section detection missed |

**Sample healthy parse log:**

```json
{"event": "docling_start",    "pdf": "Woodbury Bylaws.pdf"}
{"event": "docling_complete", "pdf": "Woodbury Bylaws.pdf",
 "parsed_pages": 27, "needs_review": false}
{"event": "stage_complete",   "stage": "parse",
 "duration_ms": 82587, "parsed_pages": 27}
```

---

### Stage 3 — Chunk

**Artifacts to check:**

```bash
python3 -c "
import json
data = json.loads(open('hoa-docs/chunks/<hoa_id>/<doc_id>_chunks.json').read())
chunks = data['chunks']
print('chunk_count:', len(chunks))
print('pipeline_status:', data.get('pipeline_status'))

# Token distribution
toks = [c['token_count'] for c in chunks]
print(f'tokens: min={min(toks)} avg={sum(toks)//len(toks)} max={max(toks)}')
over = [c for c in chunks if c['token_count'] > 1000]
print(f'over-limit chunks (>1000 tokens): {len(over)}')

# Section coverage
with_section = [c for c in chunks if c.get('section')]
print(f'chunks with section: {len(with_section)} / {len(chunks)}')

# Ancestor chain
with_anc = [c for c in chunks if c.get('ancestor_chain')]
print(f'chunks with ancestor_chain: {len(with_anc)} / {len(chunks)}')

# BBox quality
with_bbox = [c for c in chunks if c.get('bounding_box') not in (None, [0,0,0,0])]
print(f'chunks with real bbox: {len(with_bbox)} / {len(chunks)}')
"
```

**What to look for:**

| Indicator | Healthy | Problem |
|-----------|---------|---------|
| `chunk_count` | > 0 | 0 → pipeline aborts |
| `pipeline_status` | `"tagged"` | `"chunked"` → stage 4 didn't run |
| Max token count | ≤ 1000 | > 1000 → oversized chunk (will be split) |
| Chunks with section | > 80% | < 50% → heading detection missed many sections |
| Chunks with ancestor_chain | > 80% | Low → Article headings not detected |
| Chunks with real bbox | > 60% | All zeros → layout JSON was empty |

---

### Stage 4 — Tag

Stage 4 writes into the same chunks file. The `pipeline_status` field is the tag:

```bash
python3 -c "
import json
data = json.loads(open('hoa-docs/chunks/<hoa_id>/<doc_id>_chunks.json').read())
chunks = data['chunks']
print('pipeline_status:', data['pipeline_status'])   # must be 'tagged'

# Spot-check metadata on first chunk with a section
c = next((x for x in chunks if x.get('section')), chunks[0])
print('authority_rank:', c.get('authority_rank'))
print('hoa_id:', c.get('hoa_id'))
print('document_type:', c.get('document_type'))
print('section:', c.get('section'))
print('ancestor_chain:', c.get('ancestor_chain'))
"
```

---

### Stage 5 — Index

**Sentinel check (confirms stage 5 completed):**

```bash
ls hoa-docs/chroma_db/.indexed/<doc_id>
cat hoa-docs/chroma_db/.indexed/<doc_id>
# → {"doc_id": "...", "hoa_id": "...", "indexed_count": 66}
```

**BM25 index files:**

```bash
ls hoa-docs/bm25_index/<hoa_id>/
# Expected: bm25_index.pkl  bm25_obj.pkl  chunk_ids.json

python3 -c "
import json
ids = json.loads(open('hoa-docs/bm25_index/<hoa_id>/chunk_ids.json').read())
print('BM25 corpus size:', len(ids))
"
```

---

## 5. End-to-End QA with qa_index.py

`qa_index.py` is a one-command QA tool that checks Chroma, BM25, and fires a hybrid search query.

### List all docs for a HOA (use pipeline_cleanup.py)

```bash
.venv/bin/python pipeline_cleanup.py --hoa-id woodbury-001
```

**Sample output:**

```
────────────────────────────────────────────────────────────────────────────────
  HOA: woodbury-001  (5 documents)
────────────────────────────────────────────────────────────────────────────────
  FILENAME                                 DOC_ID     STAGE      PARSED CHUNKS INDEXED
  ──────────────────────────────────────── ────────── ────────── ────── ────── ───────
  Parking Rules.pdf                        fbe2e740…  rules      md=✓ lay=✓       ✓1       ✓
  Woodbury Bylaws.pdf                      553a2cdf…  bylaws     md=✓ lay=✓      ✓66       ✓
  Woodbury CC&Rs.pdf                       8463f7de…  ccr        md=✓ lay=✓     ✓209       ✓

  Chroma 'hoa_woodbury-001': 366 total chunks
  BM25  'woodbury-001': 366 total chunks
```

### Full QA check for one document

```bash
.venv/bin/python qa_index.py \
  --hoa-id woodbury-001 \
  --doc-id 553a2cdf-3419-44ae-bb53-3888b685f10b \
  --query  "board of directors voting quorum"
```

**Sample healthy output (Woodbury Bylaws, Docling parser):**

```
============================================================
[Chroma] collections present: ['hoa_woodbury-001']
[Chroma] 'hoa_woodbury-001' total chunks: 366
[Chroma] chunks for doc_id=553a2cdf-...: 66
[Chroma] pages represented: [1, 7, 8, 9, 10, 13, 14, 17, 18, 19, 21, 22, 23, 24, 27]
[Chroma] chunk types: {'rule': 66}
[Chroma] authority_rank: 4
[Chroma] document_type: bylaws

[Chroma] sample chunks:
  [1] id=553a2cdf-...::::0
       page=1  section=''  type=rule
       text: 'BYLAWS  OF  WOODBURY COMMUNITY ASSOCIATION  ## TABLE OF CONTENTS ...'
  [2] id=553a2cdf-...::1::1
       page=1  section='1'  type=rule
       text: 'The name of  the corporation is WOODBURY COMMUNITY ASSOCIATION ...'

[BM25]  corpus size: 366 chunk IDs
[BM25]  chunks for doc_id=553a2cdf-...: 66

[Query] 'board of directors voting quorum'  (top 5, hybrid RRF)
  [1] doc=553a2cdf…  page=1   type=bylaws  section='6'
       'Except as otherwise provided in these Bylaws or the Master Declaration,
        the presence in person of Delegates representing...'
  [2] doc=553a2cdf…  page=1   type=bylaws  section='7'
       'A majority of the total number of Directors shall constitute a quorum
        for the transaction of business...'
  [3] doc=8463f7de…  page=1   type=ccr     section='IV'
       '#### (f) Quorum. The presence, in person or by proxy, of Members
        representing at least twenty-five percent (25%)...'
============================================================
```

### HOA-wide query (no doc filter)

```bash
.venv/bin/python qa_index.py \
  --hoa-id woodbury-001 \
  --query  "pet restrictions noise nuisance"
```

**Sample output:**

```
[Query] 'pet restrictions noise nuisance'  (top 5, hybrid RRF)
  [1] doc=8463f7de…  page=109  type=ccr  section='VIII'
       '## USE RESTRICTIONS  Save and except for Declarant...'
  [2] doc=8463f7de…  page=111  type=ccr  section='6'
       'An Owner may keep within his respective Lot or Condominium:
        (i) common domesticated household animals (e.g...'
  [3] doc=8463f7de…  page=112  type=ccr  section='6'
       'Section 7. Quiet Enjoyment. No Owner shall permit or allow
        any activity to be performed...'
```

### QA assertions to verify

| Check | Pass condition |
|-------|---------------|
| Collection exists | `[Chroma] collections present` includes `hoa_<hoa_id>` |
| Chunk count exact | Chunks for `doc_id` == `indexed_count` in sentinel file |
| Chroma == BM25 | Both show the same corpus size |
| Page spread | Pages list has multiple distinct values (not all `1`) |
| No oversized chunks | Max token count ≤ 1000 |
| Query relevance | Top result text is semantically related to the query |
| Cross-doc results | HOA-wide query surfaces CC&Rs (rank 2) above bylaws (rank 4) |

---

## 6. Parser Output Quality

The pipeline uses **Docling** (IBM, Apache 2.0) as the primary parser. It runs fully locally with no API credits. Model weights (~500 MB) are downloaded once on first run.

### Docling vs. LlamaParse — Woodbury Bylaws comparison

| Metric | LlamaParse (legacy) | Docling (current) |
|--------|--------------------|--------------------|
| H2 article headings | 15 | 29 |
| H3 section headings | 13 (noisy TOC items) | 61 (clean Section N.N headings) |
| Chunks produced | 36 | 66 |
| Max chunk tokens | 1,418 | 802 |
| Chunks over 1,000 tokens | 4 | 0 |
| Chunks with section populated | 31 / 36 | 65 / 66 |
| Full `ancestor_chain` | partial | ✓ all |

### Current production index (woodbury-001)

| Document | Pages | Chunks | Chunk types | Notes |
|----------|-------|--------|-------------|-------|
| Woodbury CC&Rs | 186 / 186 | 209 | rule (207), definition (1), table (1) | Full layout + Article/Section hierarchy |
| Woodbury Bylaws | 27 / 27 | 66 | rule (66) | 61 H3 section headings, 0 oversized |
| Parking Rules | 2 / 2 | 1 | rule (1) | Flat numbered list — single chunk is correct (~600 tokens) |

### Heading detection for HOA documents

The Docling parser applies legal-document heading patterns:

| Pattern | Heading level | Example |
|---------|--------------|---------|
| `ARTICLE I`, `Article IV` | H2 | Major structural division |
| `Section 4`, `Section 4.2` | H3 | Numbered section |
| `Section 4.2.1`, `4.2.1 Title` | H4 | Sub-section |
| `Section N. Title. Body text…` (inline) | H3 + body paragraph | Common in bylaws |

**Inline section extraction** — bylaws often render sections as a single paragraph:

```
Section 1. Name and Location. The name of the corporation is WOODBURY...
```

The parser detects this pattern and splits it into a proper H3 heading + body text, so the section number appears in the chunk's `section` field and the `ancestor_chain`.

### Known limitations

- **Flat rules documents** (like Parking Rules): no Article/Section heading hierarchy in the source PDF → all content falls into one chunk. This is expected; the chunk is well under the 1,000-token limit.
- **OCR artifacts**: garbled roman numerals (e.g. `ARTICLE IV` → `ARTICLEN` in some scanned PDFs) are tagged as `ARTICLE [OCR?:N]` and still promoted to H2 so heading hierarchy is preserved.

---

## 7. Cache Cleanup & Re-processing

### pipeline_cleanup.py

`pipeline_cleanup.py` is the canonical tool for clearing cache and re-triggering the pipeline. It safely purges Chroma and BM25 entries (not just file deletion) before re-indexing, preventing duplicate chunks.

```bash
# List all docs and their pipeline state
.venv/bin/python pipeline_cleanup.py --hoa-id woodbury-001

# Preview what a full re-parse would delete (no changes made)
.venv/bin/python pipeline_cleanup.py \
  --hoa-id woodbury-001 \
  --doc-id 553a2cdf-3419-44ae-bb53-3888b685f10b \
  --dry-run

# Full re-parse from stage 2 (parse → chunk → tag → index)
.venv/bin/python pipeline_cleanup.py \
  --hoa-id woodbury-001 \
  --doc-id 553a2cdf-3419-44ae-bb53-3888b685f10b

# Re-chunk + re-index only (keep existing .md and _layout.json)
.venv/bin/python pipeline_cleanup.py \
  --hoa-id woodbury-001 \
  --doc-id 553a2cdf-3419-44ae-bb53-3888b685f10b \
  --from-stage 3

# Re-index only (keep .md, layout, and chunks — re-embed into Chroma/BM25)
.venv/bin/python pipeline_cleanup.py \
  --hoa-id woodbury-001 \
  --doc-id 553a2cdf-3419-44ae-bb53-3888b685f10b \
  --from-stage 5
```

After cleanup, the script prints the exact CLI command to re-run. For example:

```
Done. Now re-run the pipeline:

  .venv/bin/python -m pipeline.cli \
    "hoa-docs/raw/woodbury-001/Woodbury Bylaws.pdf" \
    --hoa-id   woodbury-001 \
    --hoa-name "<HOA Display Name>" \
    --document-type bylaws
```

### What `--from-stage` deletes

| `--from-stage` | Files deleted | Index entries purged |
|----------------|--------------|----------------------|
| `2` (default)  | `.md`, `_layout.json`, `_chunks.json`, sentinel | Chroma + BM25 |
| `3`            | `_chunks.json`, sentinel | Chroma + BM25 |
| `5`            | sentinel only | Chroma + BM25 |

> **Important:** Stage 5 always purges existing Chroma and BM25 entries for the `doc_id` before inserting new ones. You do not need to manually clean these — the cleanup script handles it. NEVER delete the sentinel alone without purging Chroma/BM25, or re-indexing will create duplicate chunks.

### Manual cleanup (without pipeline_cleanup.py)

If you must do it by hand:

```bash
DOC_ID="553a2cdf-3419-44ae-bb53-3888b685f10b"
HOA_ID="woodbury-001"

# 1. Purge Chroma entries (MUST do before deleting sentinel)
.venv/bin/python - <<EOF
import chromadb
client = chromadb.PersistentClient(path="hoa-docs/chroma_db")
col = client.get_collection(f"hoa_{HOA_ID}")
col.delete(where={"doc_id": "$DOC_ID"})
print("Chroma:", col.count(), "chunks remaining")
EOF

# 2. Delete file cache
rm hoa-docs/chroma_db/.indexed/$DOC_ID
rm hoa-docs/chunks/$HOA_ID/${DOC_ID}_chunks.json
rm hoa-docs/parsed/$HOA_ID/${DOC_ID}.md
rm hoa-docs/parsed/$HOA_ID/${DOC_ID}_layout.json

# 3. Re-run
.venv/bin/python -m pipeline.cli \
  "hoa-docs/raw/$HOA_ID/Woodbury Bylaws.pdf" \
  --hoa-id $HOA_ID --hoa-name "Woodbury Community" --document-type bylaws
```

---

## 8. Inspecting the Database

Connect to the local Postgres instance (port 5433, mapped from Docker):

```bash
psql "postgresql://postgres:postgres@127.0.0.1:5433/hoa_compliance"
```

### Useful queries

**All documents and their pipeline status:**

```sql
SELECT doc_id, original_filename, document_type, status, page_count, created_at
FROM documents
ORDER BY created_at DESC;
```

**Stalled or failed documents:**

```sql
SELECT doc_id, original_filename, status, updated_at
FROM documents
WHERE status NOT IN ('indexed')
ORDER BY updated_at DESC;
```

**Status breakdown per HOA:**

```sql
SELECT hoa_id, status, COUNT(*) AS count
FROM documents
GROUP BY hoa_id, status
ORDER BY hoa_id, status;
```

---

## 9. Troubleshooting

### Pipeline shows `failed` in UI

```bash
# Check backend logs
docker compose logs backend --tail 50
```

### Docling produces empty content

Logged as `RuntimeError: Docling returned empty content`. The pipeline automatically retries with the pypdf fallback.

- Verify the PDF is not encrypted: `pdfinfo path/to/file.pdf`
- Very large PDFs (> 200 pages) may need more memory; check for OOM errors in logs

When the fallback runs, the layout JSON will contain `{"pages": [], "source": "fallback"}`. Chunks will have `page=1` and `bounding_box=[0,0,0,0]`. The document can still be queried, but bounding-box-based UI highlighting will not work.

### Layout JSON has `{"pages": []}` (empty, no source field)

This is a stale file from the old LlamaParse parser. The cache check treats it as a miss and will re-parse on the next run. If you want to force it immediately:

```bash
rm hoa-docs/parsed/<hoa_id>/<doc_id>_layout.json
# Re-run pipeline — will re-parse from stage 2
```

### Chunk count in Chroma doesn't match chunks file

This happens if the sentinel was deleted and the pipeline re-indexed without first purging old Chroma entries. Use `pipeline_cleanup.py --from-stage 5` to purge and re-index cleanly:

```bash
.venv/bin/python pipeline_cleanup.py \
  --hoa-id <hoa_id> --doc-id <doc_id> --from-stage 5
# Then re-run the pipeline
```

### Chroma collection missing or chunk count wrong

```bash
.venv/bin/python - <<'EOF'
import chromadb
client = chromadb.PersistentClient(path="hoa-docs/chroma_db")
for col in client.list_collections():
    print(col.name, client.get_collection(col.name).count())
EOF
```

If the count is low, use `pipeline_cleanup.py` to purge and re-index.

### `raw_path` in manifest points to a Docker path (`/app/...`)

This happens when a document was first ingested inside the Docker backend container. Stage 1 auto-corrects this on the next local run. If you need to fix it manually:

```bash
# Edit the manifest directly
nano hoa-docs/raw/<hoa_id>/.manifest.json
# Change "/app/hoa-docs/..." → "/Users/<you>/open/hbook/hoa-docs/..."
```

### BM25 index not found

```bash
ls hoa-docs/bm25_index/<hoa_id>/
# Expected: bm25_index.pkl  bm25_obj.pkl  chunk_ids.json
```

If files are missing, use `pipeline_cleanup.py --from-stage 5` to rebuild. BM25 is rebuilt from the existing Chroma corpus on each stage 5 execution.

### Sections not detected in chunks (`section` field is empty)

Check the parsed markdown for heading structure:

```bash
grep "^#" hoa-docs/parsed/<hoa_id>/<doc_id>.md | head -20
```

- If headings are present but start with `##` (H2 only, no H3): the inline Section pattern may not be matching. Check that sections are written as `Section N. Title. Body...` (capital S, number, period, title, period).
- If no headings at all: Docling classified everything as plain `TEXT`. This is common in flat rules documents — expected behaviour.
- If you see `ARTICLE [OCR?:X]` entries: the PDF has garbled roman numerals from OCR. These are still promoted to H2 and will appear in the ancestor chain — compliance queries will still work.

### DB `status` stuck at `queued` or `parsed`

The background task in the API may have crashed. Re-run via CLI to drive it to `indexed`:

```bash
.venv/bin/python -m pipeline.cli "hoa-docs/raw/<hoa_id>/<filename>.pdf" \
  --hoa-id <hoa_id> --hoa-name "<name>" --document-type <type>
```
