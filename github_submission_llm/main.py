"""Command-line interface for the FNOL claims agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claims_agent import process_document, process_document_with_llm


def main() -> None:
    parser = argparse.ArgumentParser(description="Process a FNOL TXT, JSON, or PDF document.")
    parser.add_argument("input", type=Path, help="Path to the FNOL document")
    parser.add_argument("--output", type=Path, help="Optional path for the JSON result")
    parser.add_argument("--use-llm", action="store_true", help="Use Groq for unstructured-text extraction")
    args = parser.parse_args()
    result = process_document_with_llm(args.input) if args.use_llm else process_document(args.input)
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Result written to {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
