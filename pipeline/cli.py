"""CLI entry point for the ingestion pipeline."""

import argparse
import json
import sys

from pipeline.orchestrator import run_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="HOA Compliance AI — Document Ingestion Pipeline"
    )
    parser.add_argument("pdf", help="Path to the PDF file to ingest")
    parser.add_argument("--hoa-id", required=True, help="HOA identifier (UUID or slug)")
    parser.add_argument("--hoa-name", required=True, help="HOA display name")
    parser.add_argument(
        "--document-type", required=True,
        choices=["state_statute", "ccr", "articles", "bylaws", "rules", "amendment"],
        help="Type of governance document",
    )
    parser.add_argument("--effective-date", help="Effective date (ISO format, required for amendments)")
    parser.add_argument("--supersedes-doc-id", help="Doc ID this amendment supersedes")
    parser.add_argument("--supersedes-doc-type", help="Document type of the superseded doc")

    args = parser.parse_args()

    results = run_pipeline(
        source_pdf_path=args.pdf,
        hoa_id=args.hoa_id,
        hoa_name=args.hoa_name,
        document_type=args.document_type,
        effective_date=args.effective_date,
        supersedes_doc_id=args.supersedes_doc_id,
        supersedes_doc_type=args.supersedes_doc_type,
    )

    print(json.dumps(results["summary"], indent=2))


if __name__ == "__main__":
    main()
