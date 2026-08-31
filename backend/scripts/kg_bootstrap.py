"""Build the knowledge-graph indices and seed the research queue (PLATFORM-SPEC.md §5 Phase 4 task 4).

    python scripts/kg_bootstrap.py

With Neo4j reachable (docker compose `neo4j`), Graphiti's indices/constraints are
created; otherwise the local SQLite store is used and nothing needs building.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

import database  # noqa: E402
from agent import research  # noqa: E402
from knowledge import graph  # noqa: E402


def main() -> int:
    database.init_db()
    print("knowledge:", graph.bootstrap())
    print("seeded topics:", research.seed_queue())
    print("status:", graph.status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
