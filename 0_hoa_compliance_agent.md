# HOA Compliance AI Agent — Implementation Guide

**Audience:** Engineering Team
**Document type:** Technical implementation roadmap
**Status:** v1.0 — Pre-build specification

---

## 1. Vision & Value Proposition

We are building a **Compliance Intelligence System** — not a chatbot, not a document search tool, and not a summarizer. The product produces a **defensible audit trail** for HOA enforcement decisions, allowing property managers, board members, and residents to know:

- Whether a situation constitutes a violation
- Which rule (and which authority level) applies
- Whether the rule is enforceable given state law
- How to respond, with cited proof

**Strategic shift:** Move from "PDF search" to **automated legal reasoning**.

**Defensible moat:**
- Structured rule understanding across document types
- Explainability with section-level citations
- Cross-document precedence (state law → CC&Rs → rules)
- Trust and grounding — every answer is provable

---

## 2. Core Problem

HOA governance documents are:

- **Long** — 50 to 300+ pages per HOA
- **Ambiguous** — terms like "reasonable," "excessive," "visible from street"
- **Hierarchical** — state statutes override CC&Rs override bylaws override rules
- **Conflicting** — amendments and board-enacted rules often contradict the original Declaration
- **Inaccessible** — non-experts cannot reliably interpret them

The system must answer four questions with proof:

1. Is this a violation?
2. What rule applies?
3. Can this rule be enforced (given state law)?
4. How should we respond?

---

## 3. The Hierarchy of Authority (Core Logic)

Every retrieval and reasoning decision must respect this precedence. Higher-ranked sources override lower-ranked sources when conflicts arise.

| Rank | Source | Description |
|------|--------|-------------|
| 1 | **State / Federal Statutes** | e.g., Davis-Stirling Act (CA), Texas Property Code Ch. 209. Highest priority. Cannot be overridden by HOA documents. |
| 2 | **Recorded Declaration (CC&Rs)** | The primary property contract recorded against the deed. |
| 3 | **Articles of Incorporation** | Corporate foundation of the HOA. |
| 4 | **Bylaws** | Governance, meetings, voting procedures. |
| 5 | **Rules & Regulations** | Board-enacted specifics (pool hours, parking permits, etc.). |

**Engineering implication:** Every indexed chunk carries an `authority_rank` field. The retriever must surface higher-rank conflicts even when the user query semantically matches a lower-rank document.

---

## 4. System Architecture

### Layer 1 — Data Ingestion & Enrichment

**Document parser — primary: LlamaParse (Premium mode); challenger: LandingAI ADE.**

Decision rationale (full benchmark protocol in Section 6):

- **LlamaParse Premium** is selected as primary because it is native to our LlamaIndex RAG framework, emits clean markdown with heading hierarchy preserved (which maps directly to our `parent_section` and `section` metadata), handles scanned + mixed-quality documents reliably, and prices at ~$0.003–0.03/page — making full ingestion of a 200-page CC&R cost under $6.
- **LandingAI ADE** is the formal challenger because it exposes bounding-box coordinates as a first-class output (semantic chunks with page numbers and coordinates), which directly serves the Evidence Card UI requirement.
- **Azure AI Document Intelligence** and **Google Document AI** are explicitly out-of-scope for MVP. Azure adds ecosystem friction against our LlamaIndex + OpenAI + Chroma stack; Google's Contract Parser is optimized for commercial-contract field extraction, not hierarchical regulatory text.
- **Unstructured.io** is retained only as the open-source benchmark baseline.

Requirements the parser must meet:
- Preserve section hierarchy (Article → Section → Subsection)
- Preserve table structure, lists, and definition blocks
- Return bounding-box coordinates per text element
- Handle low-quality scans and handwritten amendments

**Context Parent Strategy (recursive retrieval):**
- When a chunk is retrieved, automatically fetch its parent section heading and any referenced definitions
- Example: a query matching "Section 4.2.1 — RV Parking" should also pull "Article IV: Architectural Control" and the "Definitions" section's entry for "Recreational Vehicle"

### Layer 2 — RAG Engine

**Framework:** LlamaIndex (core retrieval and reasoning)

**Retrieval strategy — Hybrid Search:**
- Semantic vector search for meaning ("can I park my camper")
- BM25 keyword matching for exact references ("Section 4.2.1," "Article IV")
- Combine results with reciprocal rank fusion

**Metadata layer:** Every chunk tagged with `authority_rank`, `effective_date`, `document_type`, `parent_section`, and `doc_id`. See Section 6 for the full schema.

### Layer 3 — LLM Reasoner

**Provider:**
- **MVP:** OpenAI (best reasoning quality for legal text)
- **Later:** local / open-weight models for cost and privacy on enterprise deployments

**Step-Back Prompting:**
- Before searching, the LLM identifies the *category* of the question (parking, architectural, pets, noise, assessments, etc.)
- This ensures retrieval pulls relevant state-law overrides even when the user's question only mentions a CC&R term
- Example: user asks about "satellite dishes" → category is "architectural restrictions" → system retrieves both CC&R Section X *and* the FCC OTARD rule that may preempt it

### Layer 4 — Vector Database

- **MVP:** Chroma (simple, embedded, fast iteration)
- **Scale:** Weaviate or Pinecone (multi-tenant, hybrid search native)

### Layer 5 — Backend API

Responsibilities:
- Document ingestion endpoints (upload, parse, chunk, index)
- Query endpoint (returns structured JSON, never free text to clients)
- Rule precedence and conflict-resolution logic
- Output formatting and citation mapping

### Layer 6 — Frontend

- Web app (React)
- Core flows: upload documents, ask questions, view evidence cards, request human review
- See Section 8 (Phase 3) for UI specifics

---

## 5. Output Design (Strict Contract)

Every response MUST conform to this JSON schema. No free-form prose responses are returned to clients.

```json
{
  "violation": true,
  "confidence": 0.91,
  "primary_rule": {
    "source": "CC&R",
    "section": "4.2.1",
    "authority_rank": 2,
    "text_snippet": "Recreational vehicles shall not be parked..."
  },
  "state_law_override": null,
  "conflict_alert": false,
  "explanation": "Parking of RVs is restricted unless screened from view per CC&R Section 4.2.1.",
  "citations": [
    {
      "doc_id": "ccr_v3_2019",
      "section": "4.2.1",
      "page": 27,
      "bounding_box": [120, 340, 480, 410]
    }
  ],
  "needs_human_review": false,
  "recommended_action": "Issue first-notice letter referencing Section 4.2.1."
}
```

**Non-negotiable principles:**
- No vague answers — every field must be populated or explicitly null
- Every claim must be cited to a specific section and page
- Confidence scores below 0.85 automatically set `needs_human_review: true`
- The LLM is **prohibited** from answering if no grounding citation exists in the retrieved context

---

## 6. Data Pipeline & Metadata Schema

This section is the **engineering blueprint** for the ingestion pipeline. The goal is to take a raw PDF (CC&R, bylaws, rules, amendments, or state statute) and produce a fully metadata-tagged, hybrid-indexed corpus that the retrieval layer can query with authority-rank precedence and recursive parent context.

### 6.0 — Parser Selection Benchmark (Pre-Build, ~3 days)

Before writing pipeline code, run this benchmark to confirm the LlamaParse-primary decision and lock in the chunking strategy. Decisions made here determine the rest of Section 6.

**Benchmark corpus** (3 documents, deliberately varied):
1. A clean digital-native CC&R (modern, ~100 pages)
2. A 1990s-era scanned CC&R (poor OCR quality, multi-column where present)
3. A recorded amendment with handwritten margin notes or signatures (worst case)

**Run each through three parsers:**
- **LlamaParse Premium** (primary candidate)
- **LandingAI ADE** (challenger)
- **Unstructured.io** (open-source baseline)

**Score each parser on four axes (1–5 scale, weighted):**

| Axis | Weight | What to measure |
|------|--------|-----------------|
| Hierarchy retention | 40% | Are `Article IV → Section 4.2 → 4.2.1` structures preserved as heading levels? |
| Table fidelity | 20% | Do assessment schedules, fine schedules, architectural standards survive intact? |
| Bounding-box accuracy | 25% | Do returned coordinates correctly highlight cited text in the source PDF? |
| Scan/handwriting recovery | 15% | What % of words in the worst-case document are recovered correctly? |

**Decision rule:** if LlamaParse scores within 5 weighted points of LandingAI, pick LlamaParse — the LlamaIndex integration savings outweigh marginal accuracy gains. If LandingAI wins by more than 5 points specifically on hierarchy or bounding boxes, escalate to a tech-lead review.

**Deliverable:** a written decision memo with scores, locked into the repo at `/docs/parser-decision.md`.

---
### 6.1 — Pipeline Architecture Overview


The ingestion pipeline is a **5-stage sequential flow** with idempotent stages (each stage can be re-run on its output). Each stage writes to durable storage so failures are recoverable without reprocessing the previous stages.

```
┌─────────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  1. Upload  │──▶│ 2. Parse │──▶│ 3. Chunk │──▶│ 4. Tag   │──▶│ 5. Index │
│  & Validate │   │  (Llama- │   │  by      │   │  Metadata│   │  (Vector │
│             │   │  Parse)  │   │  Section │   │          │   │  + BM25) │
└─────────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
       │                │              │              │              │
       ▼                ▼              ▼              ▼              ▼
   S3: raw/         S3: parsed/   S3: chunks/    Postgres:      Chroma +
   {doc_id}.pdf     {doc_id}.md   {doc_id}.json  metadata        BM25 index
                    + layout.json                  table
```

**Storage layout:**
- `s3://hoa-docs/raw/{hoa_id}/{doc_id}.pdf` — original PDF (immutable)
- `s3://hoa-docs/parsed/{hoa_id}/{doc_id}.md` — LlamaParse markdown output
- `s3://hoa-docs/parsed/{hoa_id}/{doc_id}_layout.json` — bounding boxes + page map
- `s3://hoa-docs/chunks/{hoa_id}/{doc_id}_chunks.json` — sectioned chunks
- Postgres `chunks` table — full metadata, primary source of truth
- Chroma collection per HOA — vector embeddings + metadata copy
- BM25 index per HOA — keyword index for hybrid retrieval

---

### 6.2 — Stage 1: Upload & Validation

**Endpoint:** `POST /api/v1/documents`

**Inputs:**
- `file` — PDF binary (multipart/form-data)
- `hoa_id` — required
- `document_type` — required, one of: `state_statute`, `ccr`, `articles`, `bylaws`, `rules`, `amendment`
- `effective_date` — required for `amendment`; optional otherwise
- `supersedes_doc_id` — optional, for amendments that replace prior versions

**Validation steps:**
1. File is a valid PDF (magic-byte check, not just extension)
2. File size ≤ 100 MB
3. Page count ≤ 500 (anything larger requires manual review — flag and reject)
4. PDF is not encrypted (or password is provided)
5. `document_type` and `effective_date` combination is valid

**On success:**
- Generate `doc_id` (UUID v4)
- Store raw PDF at `s3://hoa-docs/raw/{hoa_id}/{doc_id}.pdf`
- Insert `documents` row in Postgres with `status = 'queued'`
- Enqueue parsing job (Celery / SQS / equivalent)
- Return `202 Accepted` with `doc_id` and status polling URL

---

### 6.3 — Stage 2: Parse with LlamaParse

**Configuration (LlamaParse Premium mode):**

```python
from llama_parse import LlamaParse

parser = LlamaParse(
    api_key=os.environ["LLAMA_CLOUD_API_KEY"],
    result_type="markdown",          # critical: markdown preserves heading hierarchy
    premium_mode=True,               # required for scanned + complex layouts
    parsing_instruction=(
        "This is a homeowners association governance document "
        "(CC&R, bylaws, or amendment). Preserve all section numbers "
        "(e.g., 'Article IV', 'Section 4.2.1') as markdown headings. "
        "Preserve all tables, definitions, and numbered lists exactly."
    ),
    extract_charts=False,
    take_screenshot=True,            # enables bounding-box layout export
    annotate_links=False,
    language="en",
)

documents = parser.load_data("path/to/source.pdf")
```

**Outputs to capture:**
1. **Markdown text** → `s3://.../parsed/{doc_id}.md`
2. **Layout JSON** with per-element bounding boxes and page numbers → `s3://.../parsed/{doc_id}_layout.json`

**Layout JSON structure** (LlamaParse returns this via `get_json_result()`):
```json
{
  "pages": [
    {
      "page": 1,
      "items": [
        {
          "type": "heading",
          "lvl": 1,
          "value": "Article IV — Architectural Control",
          "bbox": [120, 80, 480, 110],
          "md": "# Article IV — Architectural Control"
        },
        {
          "type": "text",
          "value": "Section 4.2.1. Recreational vehicles shall not...",
          "bbox": [120, 340, 480, 410],
          "md": "Section 4.2.1. Recreational vehicles shall not..."
        }
      ]
    }
  ]
}
```

**Failure handling:**
- LlamaParse timeout (>5 min): retry once with `premium_mode=False`, flag for manual review
- LlamaParse returns empty markdown: hard failure, mark `status = 'parse_failed'`, alert
- Page count mismatch (parsed pages ≠ source pages): flag for manual review

**On success:** update `documents.status = 'parsed'` in Postgres.

---

### 6.4 — Stage 3: Section-Aware Chunking

**Principle:** Never split by character count alone. Split by **structural boundaries** that LlamaParse already identified, then enforce size caps within those boundaries.

**Algorithm:**

```
1. Walk the markdown AST top-to-bottom.
2. Identify section anchors using regex on heading text:
     - r'^Article\s+([IVX]+|\d+)' → top-level (depth 1)
     - r'^Section\s+(\d+(\.\d+)*)' → mid-level (depth 2)
     - r'^(\d+(\.\d+)+)' → numbered subsection (depth 3+)
3. For each leaf section:
     a. Collect all content (paragraphs, lists, tables) until next heading.
     b. If content ≤ 1000 tokens → emit as single chunk.
     c. If content > 1000 tokens → split at paragraph boundaries with 100-token overlap.
        Each split-chunk inherits the same section metadata.
4. For every chunk, attach the full ancestor chain
   (e.g., "Article IV → Section 4 → Section 4.2 → Section 4.2.1").
5. Cross-reference the layout JSON to attach bounding boxes for each chunk.
```

**Special handling — the Definitions section:**
- Detect via heading match (`/^Definitions?$/i` or `/^Article.*Definitions/i`)
- Each defined term becomes its **own chunk** with `chunk_type = 'definition'`
- Definition chunks are retrieved alongside any chunk that references the term (Context Parent Strategy)

**Special handling — tables:**
- Tables are kept as a single chunk regardless of size (do not split tables)
- If a table exceeds 1000 tokens, set `chunk_type = 'oversized_table'` and flag for review

**Output schema** (`{doc_id}_chunks.json`):
```json
{
  "doc_id": "ccr_v3_2019_abc123",
  "chunks": [
    {
      "chunk_id": "ccr_v3_2019_abc123::4.2.1::0",
      "text": "Section 4.2.1. Recreational vehicles shall not be parked...",
      "chunk_type": "rule",
      "section": "4.2.1",
      "ancestor_chain": ["Article IV — Architectural Control", "Section 4 — Restrictions", "Section 4.2 — Vehicle Storage"],
      "page": 27,
      "bounding_box": [120, 340, 480, 410],
      "token_count": 247
    }
  ]
}
```

`chunk_type` enum: `rule`, `definition`, `table`, `recital`, `oversized_table`.

---

### 6.5 — Stage 4: Metadata Tagging

**Every chunk** must carry the full schema below before indexing. Missing required fields = hard rejection from the index.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `chunk_id` | string | yes | Globally unique: `{doc_id}::{section}::{ordinal}` |
| `doc_id` | string | yes | Document UUID |
| `hoa_id` | string | yes | HOA tenant identifier |
| `hoa_name` | string | yes | Display name |
| `document_type` | enum | yes | `state_statute`, `ccr`, `articles`, `bylaws`, `rules`, `amendment` |
| `authority_rank` | int (1–5) | yes | Derived from `document_type` per Section 3 |
| `section` | string | yes | e.g., `"4.2.1"`; empty string for preamble/recitals |
| `parent_section` | string | yes | Top-level ancestor heading |
| `ancestor_chain` | array<string> | yes | Full path from root to this section |
| `chunk_type` | enum | yes | `rule`, `definition`, `table`, `recital`, `oversized_table` |
| `defined_terms` | array<string> | no | Terms defined in this chunk (only for `chunk_type = 'definition'`) |
| `referenced_terms` | array<string> | no | Defined terms referenced from elsewhere (extracted via NER) |
| `effective_date` | ISO date | conditional | Required for `amendment`; nullable otherwise |
| `supersedes_doc_id` | string | no | If this amends a prior document |
| `last_updated` | ISO datetime | yes | Index timestamp |
| `page` | int | yes | Source PDF page (1-indexed) |
| `bounding_box` | array<float>[4] | yes | `[x1, y1, x2, y2]` in PDF coordinate space |
| `token_count` | int | yes | For retrieval budget calculations |
| `text` | string | yes | The chunk content |

**`authority_rank` derivation table (hardcoded):**

```python
AUTHORITY_RANK = {
    "state_statute": 1,
    "ccr":           2,
    "articles":      3,
    "bylaws":        4,
    "rules":         5,
    "amendment":     None,  # inherits from supersedes_doc_id
}
```

**Amendment rank inheritance:** if `document_type = 'amendment'` and `supersedes_doc_id` is set, the amendment chunks inherit the rank of the document they amend (typically rank 2, CC&R amendments). Flag any amendment without a `supersedes_doc_id` for manual review.

**Postgres schema (`chunks` table):**
```sql
CREATE TABLE chunks (
    chunk_id          TEXT PRIMARY KEY,
    doc_id            UUID NOT NULL REFERENCES documents(doc_id),
    hoa_id            UUID NOT NULL,
    document_type     TEXT NOT NULL,
    authority_rank    SMALLINT NOT NULL CHECK (authority_rank BETWEEN 1 AND 5),
    section           TEXT NOT NULL,
    parent_section    TEXT NOT NULL,
    ancestor_chain    TEXT[] NOT NULL,
    chunk_type        TEXT NOT NULL,
    defined_terms     TEXT[],
    referenced_terms  TEXT[],
    effective_date    DATE,
    supersedes_doc_id UUID,
    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    page              INTEGER NOT NULL,
    bounding_box      REAL[4] NOT NULL,
    token_count       INTEGER NOT NULL,
    text              TEXT NOT NULL
);

CREATE INDEX idx_chunks_hoa_rank   ON chunks(hoa_id, authority_rank);
CREATE INDEX idx_chunks_doc        ON chunks(doc_id);
CREATE INDEX idx_chunks_section    ON chunks(hoa_id, section);
CREATE INDEX idx_chunks_terms_gin  ON chunks USING GIN(referenced_terms);
```

---

### 6.6 — Stage 5: Hybrid Indexing

Two indexes are built **in lockstep** — both must succeed or the chunk is not considered indexed.

**Index A — Vector (Chroma):**

```python
from chromadb import Client
from llama_index.embeddings.openai import OpenAIEmbedding

embed_model = OpenAIEmbedding(model="text-embedding-3-small")  # 1536 dims, cost-efficient

collection = chroma_client.get_or_create_collection(
    name=f"hoa_{hoa_id}",
    metadata={"hnsw:space": "cosine"}
)

collection.add(
    ids=[chunk["chunk_id"] for chunk in chunks],
    documents=[chunk["text"] for chunk in chunks],
    embeddings=[embed_model.get_text_embedding(c["text"]) for c in chunks],
    metadatas=[{
        "doc_id": c["doc_id"],
        "document_type": c["document_type"],
        "authority_rank": c["authority_rank"],
        "section": c["section"],
        "parent_section": c["parent_section"],
        "chunk_type": c["chunk_type"],
        "effective_date": c.get("effective_date"),
        "page": c["page"],
    } for c in chunks]
)
```

**Index B — BM25 (keyword):**

```python
from rank_bm25 import BM25Okapi

# tokenize and index per HOA; serialize to disk
tokenized = [tokenize(c["text"]) for c in chunks]
bm25 = BM25Okapi(tokenized)
save_bm25_index(hoa_id, bm25, chunk_id_map)
```

**Why both indexes are required:**
- **Vector** catches semantic queries: *"can I park my camper out front"* → matches "Section 4.2.1 — Recreational Vehicles"
- **BM25** catches exact-reference queries: *"what does Section 4.2.1 say"* → exact section number match

**Hybrid retrieval at query time** uses Reciprocal Rank Fusion (RRF) to merge both result sets:

```
score(chunk) = (1 / (k + vector_rank)) + (1 / (k + bm25_rank))
where k = 60 (standard RRF constant)
```

**On success:** update `documents.status = 'indexed'` in Postgres. Document is now queryable.

---

### 6.7 — Idempotency, Versioning, and Re-ingestion

**Idempotency keys:**
- `doc_id` is generated once at upload and never reused
- Re-running stages 2–5 on the same `doc_id` is safe (each stage overwrites its own outputs)

**Versioning amendments:**
- A new amendment does NOT delete prior CC&R chunks — both remain queryable
- Retrieval logic handles precedence at query time using `effective_date`
- This preserves audit history: "what did the rule say in 2018?" remains answerable

**Re-ingestion triggers:**
- Schema migration (e.g., adding a new metadata field) → re-run stage 4 + 5 only
- Parser upgrade (LlamaParse new version) → re-run stages 2–5
- Embedding model change → re-run stage 5 only

Track `pipeline_version` on each chunk to detect chunks that need re-processing after an upgrade.

---

### 6.8 — Pipeline Observability

Required logging (structured JSON logs, one event per stage transition):

```json
{
  "event": "stage_complete",
  "stage": "parse",
  "doc_id": "...",
  "hoa_id": "...",
  "duration_ms": 47200,
  "chunk_count": null,
  "page_count": 87,
  "parser": "llamaparse_premium",
  "parser_version": "0.5.x"
}
```

Required metrics (Prometheus / equivalent):
- `pipeline_stage_duration_seconds{stage, hoa_id}`
- `pipeline_stage_failures_total{stage, error_type}`
- `chunks_indexed_total{hoa_id, document_type}`
- `parser_cost_usd_total{parser}` (track LlamaParse spend)

Required alerts:
- Any stage failure (page level)
- End-to-end ingestion duration > 15 min for a single document
- Daily LlamaParse spend > $50 (early-stage threshold; tune up later)

---

### 6.9 — Acceptance Tests for the Pipeline

Before declaring Stage 1 of the roadmap complete, the pipeline must pass these tests against the **Golden Dataset** (Section 8, Phase 1):

1. **Round-trip fidelity:** ingest a CC&R, retrieve every section by exact section number via BM25, verify text matches source PDF.
2. **Hierarchy retention:** for 20 random chunks, verify `ancestor_chain` is correct against the source PDF table of contents.
3. **Bounding-box accuracy:** for 10 random chunks, render the source PDF page with the bounding box overlaid and visually verify the box surrounds the cited text.
4. **Authority-rank filter:** query with `authority_rank <= 2` and confirm zero results from `bylaws` or `rules` documents.
5. **Definition retrieval:** query a rule that references a defined term, confirm the recursive retriever surfaces both the rule chunk and the definition chunk.
6. **Amendment precedence:** ingest a CC&R + an amendment, query the amended section, confirm both versions are returned and the amendment is ranked higher.

These tests are CI gates — pipeline changes that break any of them block deployment.

---

## 7. Key Challenges & Solutions

| Challenge | Solution |
|-----------|----------|
| **Rule conflicts** (CC&R vs amendment vs state law) | Authority-rank precedence in retrieval; emit `conflict_alert: true` when multiple ranks return contradictory rules. |
| **Ambiguity** ("reasonable," "excessive") | Confidence scoring; auto-flag for human review below threshold; surface the ambiguous term in the explanation. |
| **Hallucination risk** | Strict grounding — LLM cannot answer without retrieved citation. If retrieval is empty, return `needs_human_review: true` with no rule claim. |
| **Old / handwritten scans** | Layout-aware OCR; preserve tables and lists during partitioning. |
| **Stale amendments** | `effective_date` and `last_updated` metadata; retrieval prefers most recent amendment when section numbers collide. |
| **Legal liability** | Every output is fully citable with bounding boxes; human-review fallback; no enforcement language without `authority_rank` source. |

---

## 8. Phased Development Roadmap

### Phase 1 — Proof of Accuracy (Weeks 1–3)

**Goal:** Demonstrate that retrieval is accurate before building UI.

- Build ingestion pipeline (OCR → chunking → metadata tagging → indexing)
- Hardcode the authority-rank precedence into the LlamaIndex retrieval flow
- Build a **Golden Dataset**: 20 ground-truth question-answer pairs from a real CC&R document
- Set up automated accuracy measurement against the Golden Dataset
- **Exit criteria:** ≥85% retrieval accuracy on Golden Dataset; precedence logic verified on at least 5 conflict test cases

### Phase 2 — Decision Engine (Weeks 4–7)

**Goal:** Produce structured, citable, defensible decisions.

- Enforce the strict JSON schema from Section 5 for every response
- Implement step-back prompting for category identification
- Implement recursive retrieval (Context Parent Strategy)
- Build citation highlighting — map each citation to bounding-box coordinates in the source PDF
- Implement conflict-alert logic across authority ranks
- **Exit criteria:** every Golden Dataset answer returns valid JSON with bounding-box citations; conflict alerts fire correctly on test cases

### Phase 3 — Management Dashboard (Weeks 8–12)

**Goal:** Make the decision engine usable by property managers.

- Build React frontend with three core surfaces:
  - **Upload & ingestion status** (multi-document per HOA)
  - **Query interface** with category hints
  - **Evidence Cards** — AI finding shown alongside the exact CC&R snippet and any relevant state statute, with the original PDF page rendered and the citation highlighted
- Implement "Request Human Review" button for any result with confidence < 0.85
- Multi-user roles (board member, property manager, resident)
- **Exit criteria:** end-to-end demo: upload CC&R + state statute → ask compliance question → receive evidence card with highlighted PDF citation

### Phase 4 — Post-MVP

- Auto Notice Generator (violation letters with adjustable tone)
- Violation Analyzer (text + image input — e.g., photo of an unscreened RV)
- Rule Graph (structured relationships across documents for deeper reasoning)
- Multi-document reasoning across CC&Rs + bylaws + state law in one query
- Pattern dashboard — track violations across a community, identify enforcement inconsistencies
- Workflow automation and integrations (property management platforms)

---

## 9. Technology Stack Summary

| Layer | MVP Choice | Scale Choice |
|-------|-----------|--------------|
| Document parser | **LlamaParse Premium** (primary) / LandingAI ADE (challenger, per §6.0 benchmark) | Same |
| RAG framework | LlamaIndex | LlamaIndex |
| LLM | OpenAI (GPT-4 class) | + local open-weight option |
| Embeddings | OpenAI `text-embedding-3-small` | `text-embedding-3-large` for accuracy-critical tenants |
| Vector DB | Chroma | Weaviate or Pinecone |
| Keyword index | `rank_bm25` (in-process) | OpenSearch / Elasticsearch |
| Metadata store | Postgres | Postgres (managed) |
| Object storage | S3 (raw PDFs, parsed outputs, chunks) | Same |
| Job queue | Celery + Redis | SQS + ECS workers |
| Backend | Python (FastAPI) | Same |
| Frontend | React | React |
| Hosting | Single-tenant cloud | Multi-tenant SaaS |

---

## 10. Competitive Positioning (For Engineering Context)

We are **not** building:
- A generic chatbot
- A document summarizer
- A PDF viewer with search
- A note-taking tool

We **are** building:
- A compliance decision engine
- That tells a property manager exactly *why* they can or cannot issue a fine
- Backed by a hierarchy of legal proof and explicit citations

This positioning has direct engineering consequences: free-form LLM output is not acceptable, every claim must be grounded, and citation fidelity is a first-class requirement — not a nice-to-have.

---

## 11. Immediate Next Steps

1. **Parser Benchmark (per §6.0)** — Run LlamaParse Premium vs LandingAI ADE vs Unstructured.io on three real CC&Rs (clean, scanned, handwritten amendment). Score on hierarchy retention (40%), table fidelity (20%), bounding-box accuracy (25%), scan recovery (15%). Lock decision in `/docs/parser-decision.md`.
2. **Schema Lock** — Finalize the JSON response contract from Section 5 *and* the chunk metadata schema from §6.5. Once locked, the frontend, retrieval layer, and ingestion pipeline can develop in parallel.
3. **Golden Dataset Build** — Source one real CC&R, one set of bylaws, and the relevant state statute. Author 20 question-answer pairs covering: clear violations, clear non-violations, ambiguous cases, state-law-overridden cases, and amendment-vs-original conflicts.
4. **Ingestion Pipeline MVP** — Build stages 1–5 from §6.1. Wire LlamaParse, section-aware chunking, Postgres metadata, Chroma + BM25 hybrid index. Pass all six §6.9 acceptance tests.
5. **Recursive Retriever Prototype** — Stand up a LlamaIndex recursive retriever that links "Rules" chunks to their "Definitions" chunks. Validate on the Golden Dataset.
6. **Authority-Rank Retrieval Test** — Confirm the retriever correctly prioritizes a state statute over a contradicting CC&R section in at least 5 hand-built conflict cases.

---

## 12. Long-Term Vision

Turn HOA compliance into a **predictable, defensible, standardized process**. Once the core decision engine is trusted, expansion paths include:

- Property management platform integrations
- Legal-tech partnerships (defensible audit trails for litigation)
- Insurance and risk scoring (HOAs with consistent enforcement = lower risk)
- Adjacent verticals: condo associations, commercial property covenants, deed-restricted communities

---

## Key Engineering Insight

Our moat is **not** the model and **not** the UI. Our moat is:

- Structured rule understanding across a legal hierarchy
- Explainability via grounded citations
- Trust earned through defensible, auditable decisions

Every architectural decision should be evaluated against whether it strengthens or weakens that moat.
