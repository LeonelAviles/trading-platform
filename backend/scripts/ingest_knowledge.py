"""Ingest a checked-out research repository into the local knowledge store.

Example:
  python scripts/ingest_knowledge.py ../data/knowledge/repos/ml4t \
    --name machine-learning-for-trading \
    --url https://github.com/stefan-jansen/machine-learning-for-trading \
    --license MIT
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import database
from research_agent.knowledge import ingest_repository


def revision_of(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest one trusted repository")
    parser.add_argument("path", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--license", dest="license_name")
    parser.add_argument("--revision")
    args = parser.parse_args()

    database.init_db()
    revision = args.revision or revision_of(args.path)
    result = ingest_repository(
        root=args.path, name=args.name, url=args.url,
        revision=revision, license_name=args.license_name,
    )
    print(f"Ingested {result['name']}@{result['revision'][:12]}: {result['documents']} documents, {result['chunks']} chunks")


if __name__ == "__main__":
    main()
