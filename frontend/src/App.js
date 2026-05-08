import React, { useState, useEffect, useCallback } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

// ── Upload stage metadata ─────────────────────────────────────────────────────

const STAGES = [
  { key: "upload", label: "Upload & Validate"    },
  { key: "parse",  label: "Parse  (Docling)"     },
  { key: "tag",    label: "Chunk & Tag"           },
  { key: "index",  label: "Index  (Vector + BM25)" },
];

const DONE_COUNT = { queued: 1, parsed: 2, tagged: 3, indexed: 4 };

// ── Lookup tables ─────────────────────────────────────────────────────────────

const DOC_TYPE_LABEL = {
  ccr:           "CC&R",
  bylaws:        "Bylaws",
  rules:         "Rules & Regs",
  articles:      "Articles",
  state_statute: "State Statute",
  amendment:     "Amendment",
};

const STATUS_STYLE = {
  indexed: { background: "#e8f5e9", color: "#2e7d32" },
  tagged:  { background: "#e3f2fd", color: "#1565c0" },
  parsed:  { background: "#e8eaf6", color: "#3949ab" },
  queued:  { background: "#f5f5f5", color: "#757575" },
  failed:  { background: "#ffebee", color: "#c62828" },
};

const STAGE_ICON = {
  done:    { char: "✓", color: "#2e7d32" },
  active:  { char: "◌", color: "#1565c0" },
  pending: { char: "○", color: "#bdbdbd" },
  failed:  { char: "✗", color: "#c62828" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric", month: "short", day: "numeric",
  });
}

function stageState(dbStatus, idx) {
  if (dbStatus === "failed") return idx === 0 ? "done" : "failed";
  const done = DONE_COUNT[dbStatus] ?? 0;
  if (idx < done)  return "done";
  if (idx === done) return "active";
  return "pending";
}

// ── Design tokens ─────────────────────────────────────────────────────────────

const T = {
  blue:       "#1a73e8",
  blueLight:  "#e8f0fe",
  text:       "#212121",
  muted:      "#666",
  faint:      "#9e9e9e",
  border:     "#e0e0e0",
  rowAlt:     "#fafafa",
};

const S = {
  label: { display: "block", fontWeight: 500, marginBottom: 4, color: "#444" },
  input: {
    width: "100%", padding: "8px 10px", border: `1px solid ${T.border}`,
    borderRadius: 4, boxSizing: "border-box", marginBottom: 14, fontSize: 14,
  },
  btnPrimary: {
    padding: "10px 26px", background: T.blue, color: "#fff",
    border: "none", borderRadius: 4, cursor: "pointer", fontSize: 15, fontWeight: 500,
  },
  btnGhost: {
    background: "none", border: "none", cursor: "pointer",
    color: T.blue, fontSize: 14, padding: 0,
  },
  btnMuted: {
    padding: "8px 20px", background: "#5f6368", color: "#fff",
    border: "none", borderRadius: 4, cursor: "pointer", fontSize: 14, marginTop: 16,
  },
  card: { background: "#f8f9fa", borderRadius: 8, padding: 20, marginTop: 24 },
  err:  { color: "#c62828", marginTop: 14, fontSize: 14 },
};

// ── Shared small components ───────────────────────────────────────────────────

function StageRow({ label, state }) {
  const { char, color } = STAGE_ICON[state];
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 0", borderBottom: `1px solid ${T.border}`,
    }}>
      <span style={{
        fontSize: 18, color, width: 22, textAlign: "center",
        fontWeight: state === "done" ? 700 : 400,
      }}>
        {char}
      </span>
      <span style={{ color: state === "pending" ? T.faint : T.text, fontSize: 15 }}>
        {label}
      </span>
      {state === "active" && (
        <span style={{ marginLeft: "auto", fontSize: 13, color: T.blue }}>running…</span>
      )}
    </div>
  );
}

function StatusBadge({ status }) {
  const style = STATUS_STYLE[status] || STATUS_STYLE.queued;
  return (
    <span style={{
      ...style,
      padding: "2px 9px", borderRadius: 12,
      fontSize: 12, fontWeight: 500, display: "inline-block",
    }}>
      {status}
    </span>
  );
}

function TabBar({ active, onChange }) {
  return (
    <div style={{
      display: "flex", borderBottom: `2px solid ${T.border}`, marginBottom: 28,
    }}>
      {[
        { key: "upload",  label: "Upload" },
        { key: "library", label: "HOA Library" },
      ].map(t => (
        <button key={t.key} onClick={() => onChange(t.key)} style={{
          padding: "10px 22px", border: "none", background: "none",
          cursor: "pointer", fontSize: 14, fontWeight: 500,
          color: active === t.key ? T.blue : "#5f6368",
          borderBottom: active === t.key
            ? `2px solid ${T.blue}` : "2px solid transparent",
          marginBottom: -2,
        }}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ── Upload tab ────────────────────────────────────────────────────────────────

function UploadTab() {
  const [file,      setFile]      = useState(null);
  const [hoaId,     setHoaId]     = useState("");
  const [hoaName,   setHoaName]   = useState("");
  const [docType,   setDocType]   = useState("rules");
  const [uploading, setUploading] = useState(false);
  const [error,     setError]     = useState(null);
  const [docId,     setDocId]     = useState(null);
  const [docStatus, setDocStatus] = useState(null);

  useEffect(() => {
    if (!docId || docStatus === "indexed" || docStatus === "failed") return;
    const timer = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API_URL}/api/v1/documents/${docId}`);
        setDocStatus(data.status);
      } catch (_) {}
    }, 2000);
    return () => clearInterval(timer);
  }, [docId, docStatus]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file || !hoaId.trim() || !hoaName.trim()) return;
    setUploading(true);
    setError(null);
    const form = new FormData();
    form.append("file",          file);
    form.append("hoa_id",        hoaId.trim());
    form.append("hoa_name",      hoaName.trim());
    form.append("document_type", docType);
    try {
      const { data } = await axios.post(`${API_URL}/api/v1/documents`, form);
      setDocId(data.doc_id);
      setDocStatus(data.status);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setUploading(false);
    }
  };

  const reset = () => {
    setDocId(null); setDocStatus(null); setFile(null); setError(null);
  };

  const isDone   = docStatus === "indexed";
  const isFailed = docStatus === "failed";

  return (
    <>
      {!docId && (
        <form onSubmit={handleSubmit}>
          <label style={S.label}>PDF Document</label>
          <input type="file" accept=".pdf"
                 style={{ ...S.input, padding: "6px 0" }}
                 onChange={(e) => setFile(e.target.files[0])} />

          <label style={S.label}>HOA ID</label>
          <input type="text" value={hoaId} style={S.input}
                 placeholder="e.g. woodbury-001"
                 onChange={(e) => setHoaId(e.target.value)} />

          <label style={S.label}>HOA Name</label>
          <input type="text" value={hoaName} style={S.input}
                 placeholder="e.g. Woodbury Community"
                 onChange={(e) => setHoaName(e.target.value)} />

          <label style={S.label}>Document Type</label>
          <select value={docType} style={S.input}
                  onChange={(e) => setDocType(e.target.value)}>
            <option value="state_statute">State Statute</option>
            <option value="ccr">CC&amp;R</option>
            <option value="articles">Articles of Incorporation</option>
            <option value="bylaws">Bylaws</option>
            <option value="rules">Rules &amp; Regulations</option>
            <option value="amendment">Amendment</option>
          </select>

          <button type="submit" style={S.btnPrimary}
                  disabled={uploading || !file || !hoaId.trim() || !hoaName.trim()}>
            {uploading ? "Uploading…" : "Upload & Ingest"}
          </button>
        </form>
      )}

      {error && <p style={S.err}>Error: {error}</p>}

      {docId && (
        <div style={S.card}>
          <div style={{ fontSize: 12, color: T.faint, marginBottom: 12 }}>
            doc_id: {docId}
          </div>
          <h3 style={{ margin: "0 0 14px", color: T.text }}>
            {isDone   ? "Ingestion complete ✓" :
             isFailed ? "Ingestion failed"      :
                        "Processing…"}
          </h3>
          {STAGES.map((s, idx) => (
            <StageRow key={s.key} label={s.label} state={stageState(docStatus, idx)} />
          ))}
          {(isDone || isFailed) && (
            <button style={S.btnMuted} onClick={reset}>
              Upload another document
            </button>
          )}
        </div>
      )}
    </>
  );
}

// ── Library tab ───────────────────────────────────────────────────────────────

function HoaList({ onSelect }) {
  const [hoas,    setHoas]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    axios.get(`${API_URL}/api/v1/hoas`)
      .then(r => setHoas(r.data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p style={{ color: T.muted }}>Loading…</p>;
  if (error)   return <p style={S.err}>Could not load HOAs: {error}</p>;
  if (!hoas.length) return (
    <p style={{ color: T.muted, fontSize: 14 }}>
      No HOAs yet. Upload a document to register an HOA.
    </p>
  );

  return (
    <>
      <p style={{ fontSize: 13, color: T.muted, margin: "0 0 16px" }}>
        {hoas.length} {hoas.length === 1 ? "HOA" : "HOAs"} registered
      </p>
      {hoas.map(hoa => (
        <div key={hoa.hoa_id}
          onClick={() => onSelect(hoa)}
          style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "14px 18px", marginBottom: 10,
            background: "#fff", border: `1px solid ${T.border}`, borderRadius: 8,
            cursor: "pointer",
          }}
          onMouseEnter={e => {
            e.currentTarget.style.borderColor = T.blue;
            e.currentTarget.style.boxShadow = `0 0 0 1px ${T.blue}`;
          }}
          onMouseLeave={e => {
            e.currentTarget.style.borderColor = T.border;
            e.currentTarget.style.boxShadow = "none";
          }}
        >
          <div>
            <div style={{ fontWeight: 600, fontSize: 15, color: T.text }}>
              {hoa.name}
            </div>
            <div style={{ fontSize: 12, color: T.faint, marginTop: 3 }}>
              {hoa.hoa_id}
              {" · "}
              {hoa.doc_count} {hoa.doc_count === 1 ? "document" : "documents"}
              {hoa.last_updated && <> · Updated {fmtDate(hoa.last_updated)}</>}
            </div>
          </div>
          <span style={{ color: T.faint, fontSize: 20, lineHeight: 1 }}>›</span>
        </div>
      ))}
    </>
  );
}

function HoaDetail({ hoa, onBack }) {
  const [docs,    setDocs]    = useState([]);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  useEffect(() => {
    axios.get(`${API_URL}/api/v1/hoas/${hoa.hoa_id}/documents`)
      .then(r => setDocs(r.data))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [hoa.hoa_id]);

  const indexed = docs.filter(d => d.status === "indexed").length;

  return (
    <>
      {/* Breadcrumb */}
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        marginBottom: 22, fontSize: 14,
      }}>
        <button onClick={onBack} style={S.btnGhost}>← HOAs</button>
        <span style={{ color: T.border }}>|</span>
        <span style={{ fontWeight: 600, color: T.text }}>{hoa.name}</span>
        <span style={{
          fontSize: 11, color: "#fff", background: "#78909c",
          borderRadius: 4, padding: "1px 6px", fontWeight: 500,
        }}>
          {hoa.hoa_id}
        </span>
      </div>

      {loading && <p style={{ color: T.muted }}>Loading documents…</p>}
      {error   && <p style={S.err}>Could not load documents: {error}</p>}

      {!loading && !error && !docs.length && (
        <p style={{ color: T.muted, fontSize: 14 }}>No documents found.</p>
      )}

      {!loading && !error && docs.length > 0 && (
        <>
          {/* Summary row */}
          <div style={{
            display: "flex", gap: 24, marginBottom: 18,
            padding: "12px 16px",
            background: T.blueLight, borderRadius: 6, fontSize: 13,
          }}>
            <span><strong>{docs.length}</strong> <span style={{ color: T.muted }}>total</span></span>
            <span><strong style={{ color: "#2e7d32" }}>{indexed}</strong> <span style={{ color: T.muted }}>indexed</span></span>
            {docs.length - indexed > 0 && (
              <span><strong style={{ color: "#c62828" }}>{docs.length - indexed}</strong> <span style={{ color: T.muted }}>pending / failed</span></span>
            )}
          </div>

          {/* Document table */}
          <div style={{ overflowX: "auto", borderRadius: 6, border: `1px solid ${T.border}` }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ background: "#f5f5f5" }}>
                  {["File", "Type", "Status", "Pages", "Uploaded"].map(h => (
                    <th key={h} style={{
                      textAlign: "left", padding: "9px 14px",
                      color: "#555", fontWeight: 500,
                      fontSize: 11, textTransform: "uppercase", letterSpacing: "0.6px",
                      borderBottom: `1px solid ${T.border}`,
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {docs.map((doc, i) => (
                  <tr key={doc.doc_id} style={{
                    background: i % 2 === 0 ? "#fff" : T.rowAlt,
                    borderBottom: i < docs.length - 1 ? `1px solid #f0f0f0` : "none",
                  }}>
                    <td style={{
                      padding: "11px 14px", color: T.text,
                      maxWidth: 240, overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }} title={doc.original_filename}>
                      {doc.original_filename}
                    </td>
                    <td style={{ padding: "11px 14px", color: "#555" }}>
                      {DOC_TYPE_LABEL[doc.document_type] || doc.document_type}
                    </td>
                    <td style={{ padding: "11px 14px" }}>
                      <StatusBadge status={doc.status} />
                    </td>
                    <td style={{ padding: "11px 14px", color: T.muted }}>
                      {doc.page_count ?? "—"}
                    </td>
                    <td style={{
                      padding: "11px 14px", color: T.faint, whiteSpace: "nowrap",
                    }}>
                      {fmtDate(doc.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function LibraryTab() {
  const [selected, setSelected] = useState(null);

  return selected
    ? <HoaDetail hoa={selected} onBack={() => setSelected(null)} />
    : <HoaList   onSelect={setSelected} />;
}

// ── Root ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [tab, setTab] = useState("upload");

  return (
    <div style={{
      maxWidth: 760, margin: "48px auto",
      fontFamily: "system-ui, sans-serif", padding: "0 16px",
    }}>
      <h1 style={{ marginBottom: 4, color: T.text }}>HOA Compliance AI</h1>
      <p style={{ color: T.muted, marginTop: 0, marginBottom: 24 }}>
        Upload and index HOA governance documents.
      </p>

      <TabBar active={tab} onChange={setTab} />

      {tab === "upload"  && <UploadTab />}
      {tab === "library" && <LibraryTab />}
    </div>
  );
}
