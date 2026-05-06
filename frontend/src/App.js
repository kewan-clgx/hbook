import React, { useState } from "react";
import axios from "axios";

const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000";

function App() {
  const [file, setFile] = useState(null);
  const [hoaId, setHoaId] = useState("");
  const [hoaName, setHoaName] = useState("");
  const [documentType, setDocumentType] = useState("ccr");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file || !hoaId || !hoaName) return;

    setLoading(true);
    setError(null);
    setResult(null);

    const formData = new FormData();
    formData.append("file", file);
    formData.append("hoa_id", hoaId);
    formData.append("hoa_name", hoaName);
    formData.append("document_type", documentType);

    try {
      const response = await axios.post(`${API_URL}/api/v1/documents`, formData);
      setResult(response.data);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 700, margin: "40px auto", fontFamily: "system-ui" }}>
      <h1>HOA Compliance AI</h1>
      <p>Upload governance documents for ingestion and indexing.</p>

      <form onSubmit={handleUpload}>
        <div style={{ marginBottom: 12 }}>
          <label>PDF Document: </label>
          <input
            type="file"
            accept=".pdf"
            onChange={(e) => setFile(e.target.files[0])}
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label>HOA ID: </label>
          <input
            type="text"
            value={hoaId}
            onChange={(e) => setHoaId(e.target.value)}
            placeholder="e.g. sunset-hoa-001"
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label>HOA Name: </label>
          <input
            type="text"
            value={hoaName}
            onChange={(e) => setHoaName(e.target.value)}
            placeholder="e.g. Sunset Hills HOA"
          />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label>Document Type: </label>
          <select value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
            <option value="state_statute">State Statute</option>
            <option value="ccr">CC&R</option>
            <option value="articles">Articles of Incorporation</option>
            <option value="bylaws">Bylaws</option>
            <option value="rules">Rules & Regulations</option>
            <option value="amendment">Amendment</option>
          </select>
        </div>
        <button type="submit" disabled={loading}>
          {loading ? "Processing..." : "Upload & Ingest"}
        </button>
      </form>

      {error && (
        <div style={{ marginTop: 20, color: "red" }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 20, background: "#f0f0f0", padding: 16, borderRadius: 8 }}>
          <h3>Ingestion Complete</h3>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

export default App;
