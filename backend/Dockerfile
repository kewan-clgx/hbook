FROM python:3.12-slim

WORKDIR /app

# System dependencies for PDF processing and SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api.py .
COPY pipeline/ ./pipeline/

# Create local storage directories
RUN mkdir -p hoa-docs/raw hoa-docs/parsed hoa-docs/chunks

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
