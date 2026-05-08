FROM python:3.11-slim

WORKDIR /app

# System dependencies for Docling (PDF rendering, OpenMP for inference, OpenGL for image libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    ca-certificates \
    libgomp1 \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Install CPU-only PyTorch first to avoid 2+ GB of CUDA packages.
# Docling depends on torch; installing the CPU wheel here prevents pip
# from later resolving the default CUDA-enabled torch on Linux.
RUN pip install --no-cache-dir \
    torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

# Install remaining Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Docling AI models into the image (~500 MB).
# This prevents a slow cold-start on first document ingestion.
RUN python - <<'EOF'
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
opts = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
print("Docling models ready.")
EOF

# Copy application code
COPY api.py .
COPY pipeline/ ./pipeline/

# Create persistent storage directories (supplemented by volume mounts in compose)
RUN mkdir -p \
    hoa-docs/raw \
    hoa-docs/parsed \
    hoa-docs/chunks \
    hoa-docs/bm25_index \
    hoa-docs/chroma_db

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
