"""Process every complete demonstration FNOL and print a compact routing table."""

from __future__ import annotations

from pathlib import Path

from claims_agent import process_document


def main() -> None:
    print(f"{'Case':<42} {'Route':<22} Missing")
    print("-" * 78)
    for document in sorted((Path(__file__).parent / "data" / "demo_cases").glob("*.json")):
        result = process_document(document)
        print(f"{document.stem:<42} {result['recommendedRoute']:<22} {len(result['missingFields'])}")


if __name__ == "__main__":
    main()
