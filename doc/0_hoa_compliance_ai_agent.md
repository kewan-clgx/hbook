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

**OCR / Parsing:**
- Primary candidates: **Unstructured.io** and **Azure AI Document Intelligence**
- Must preserve table structure, lists, and section hierarchy
- Must handle low-quality scans and handwritten amendments
- Action item: benchmark both providers against three real 50-page scanned CC&R documents and select based on hierarchy retention

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

### Step 1 — Document Ingestion
- Accept PDF upload
- Run OCR (if needed) via selected provider
- Preserve layout, tables, headings, lists

### Step 2 — Chunking Strategy
- Split by **section boundaries** (e.g., 4.2, 5.1.1) — never by arbitrary character count
- Preserve heading context in each chunk
- Cap chunk size at ~1000 tokens with 100-token overlap for boundary safety

### Step 3 — Metadata Tagging

Every chunk must carry the following schema:

| Field | Type | Description |
|-------|------|-------------|
| `doc_id` | string | Unique HOA + document identifier |
| `hoa_name` | string | Display name |
| `document_type` | enum | `state_statute`, `ccr`, `articles`, `bylaws`, `rules`, `amendment` |
| `authority_rank` | int (1–5) | Per Section 3 hierarchy |
| `section` | string | e.g., "4.2.1" |
| `parent_section` | string | e.g., "Article IV: Architectural Control" |
| `effective_date` | date | For amendment handling |
| `last_updated` | date | Index timestamp |
| `page` | int | Source page in original PDF |
| `bounding_box` | array | [x1, y1, x2, y2] for citation highlighting |

### Step 4 — Indexing
- Build dual indexes: dense vector + BM25 keyword
- Store metadata alongside vectors for filtered retrieval (e.g., "only retrieve chunks where `authority_rank <= 2`")

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
| OCR / Parsing | Unstructured.io *or* Azure AI Document Intelligence (TBD via benchmark) | Same |
| RAG framework | LlamaIndex | LlamaIndex |
| LLM | OpenAI (GPT-4 class) | + local open-weight option |
| Vector DB | Chroma | Weaviate or Pinecone |
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

1. **OCR Provider Selection** — Test Unstructured.io vs Azure AI Document Intelligence on three 50-page scanned CC&Rs. Score on section-hierarchy retention, table fidelity, and handwriting handling.
2. **Schema Lock** — Finalize the JSON response contract from Section 5. Once locked, the frontend and backend can develop in parallel.
3. **Golden Dataset Build** — Source one real CC&R, one set of bylaws, and the relevant state statute. Author 20 question-answer pairs covering: clear violations, clear non-violations, ambiguous cases, state-law-overridden cases, and amendment-vs-original conflicts.
4. **Recursive Retriever Prototype** — Stand up a LlamaIndex recursive retriever that links "Rules" chunks to their "Definitions" chunks. Validate on the Golden Dataset.
5. **Authority-Rank Retrieval Test** — Confirm the retriever correctly prioritizes a state statute over a contradicting CC&R section in at least 5 hand-built conflict cases.

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
