"""Local research library and provenance graph.

SQLite holds three understandable things: sources, document/section nodes, and
searchable chunks.  The graph is deliberately a provenance graph rather than
an LLM-generated collection of unverified claims.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import text

import database
from models import KnowledgeChunk, KnowledgeEdge, KnowledgeNode, KnowledgeSource, new_id, utc_now


SUPPORTED_SUFFIXES = {".md", ".rst", ".txt", ".py", ".ipynb", ".yaml", ".yml"}
SKIP_DIRS = {".git", ".github", ".venv", "__pycache__", "assets", "data", "envs", "node_modules"}
MAX_TEXT_BYTES = 2_000_000
MAX_NOTEBOOK_BYTES = 15_000_000
CHUNK_CHARS = 6_000
CHUNK_OVERLAP = 350


@dataclass(frozen=True)
class ParsedChunk:
    path: str
    heading: str
    content: str
    position: int


def _read_notebook(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    sections: list[str] = []
    for cell in doc.get("cells", []):
        source = "".join(cell.get("source") or []).strip()
        if not source:
            continue
        if cell.get("cell_type") == "code":
            sections.append(f"```python\n{source}\n```")
        elif cell.get("cell_type") == "markdown":
            sections.append(source)
    return "\n\n".join(sections)


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".ipynb":
        return _read_notebook(path)
    return path.read_text(encoding="utf-8", errors="replace")


def iter_documents(root: Path):
    """Yield safe text-like files, excluding datasets and generated assets."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            continue
        limit = MAX_NOTEBOOK_BYTES if path.suffix.lower() == ".ipynb" else MAX_TEXT_BYTES
        if path.stat().st_size <= limit:
            yield path


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _split_large(text_value: str) -> list[str]:
    if len(text_value) <= CHUNK_CHARS:
        return [text_value]
    chunks: list[str] = []
    start = 0
    while start < len(text_value):
        end = min(len(text_value), start + CHUNK_CHARS)
        if end < len(text_value):
            boundary = text_value.rfind("\n\n", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(text_value[start:end].strip())
        if end >= len(text_value):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return [c for c in chunks if c]


def chunk_document(relative_path: str, content: str) -> list[ParsedChunk]:
    """Split at Markdown headings, then cap large sections."""
    default_heading = Path(relative_path).stem.replace("_", " ").replace("-", " ")
    suffix = Path(relative_path).suffix.lower()
    if suffix not in {".md", ".ipynb"}:
        return [
            ParsedChunk(relative_path, default_heading, piece, position)
            for position, piece in enumerate(_split_large(content))
            if len(piece) >= 60
        ]
    heading = default_heading
    section: list[str] = []
    sections: list[tuple[str, str]] = []
    in_code_fence = False
    for line in content.splitlines():
        if line.strip().startswith("```"):
            in_code_fence = not in_code_fence
        match = None if in_code_fence else _HEADING.match(line)
        if match and section:
            sections.append((heading, "\n".join(section).strip()))
            section = []
        if match:
            heading = match.group(2).strip()
        section.append(line)
    if section:
        sections.append((heading, "\n".join(section).strip()))

    result: list[ParsedChunk] = []
    position = 0
    for section_heading, section_text in sections:
        for piece in _split_large(section_text):
            if len(piece) < 60:
                continue
            result.append(ParsedChunk(relative_path, section_heading, piece, position))
            position += 1
    return result


def _node(db, source_id: str, kind: str, key: str, title: str, properties: dict | None = None) -> KnowledgeNode:
    node = KnowledgeNode(
        id=new_id(), source_id=source_id, kind=kind, key=key,
        title=title, properties_json=properties, created_at=utc_now(),
    )
    db.add(node)
    db.flush()
    return node


def ingest_repository(*, root: Path, name: str, url: str, revision: str, license_name: str | None) -> dict:
    """Add a pinned version atomically; retain prior passages and their IDs."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"repository directory does not exist: {root}")

    parsed: list[tuple[str, list[ParsedChunk]]] = []
    for path in iter_documents(root):
        rel = path.relative_to(root).as_posix()
        try:
            chunks = chunk_document(rel, read_document(path))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if chunks:
            parsed.append((rel, chunks))

    with database.session_scope() as db:
        old = db.query(KnowledgeSource).filter(KnowledgeSource.name == name).one_or_none()
        if old:
            old.name = f"{old.name[:190]}@{old.revision[:12]} [{old.id}]"
            db.flush()

        source = KnowledgeSource(
            id=new_id(), name=name, url=url.rstrip("/"), revision=revision,
            license=license_name, status="ingesting", created_at=utc_now(), updated_at=utc_now(),
        )
        db.add(source)
        db.flush()
        repo_node = _node(db, source.id, "repository", "repository", name, {
            "url": source.url, "revision": revision, "license": license_name,
        })

        chunk_count = 0
        for rel, chunks in parsed:
            doc_url = f"{source.url}/blob/{quote(revision, safe='')}/{quote(rel)}"
            doc_node = _node(db, source.id, "document", f"document:{rel}", rel, {"url": doc_url})
            db.add(KnowledgeEdge(id=new_id(), from_node_id=repo_node.id, predicate="CONTAINS", to_node_id=doc_node.id))
            for chunk in chunks:
                section_node = _node(
                    db, source.id, "section", f"section:{rel}:{chunk.position}", chunk.heading,
                    {"path": rel, "position": chunk.position, "url": doc_url},
                )
                db.add(KnowledgeEdge(id=new_id(), from_node_id=doc_node.id, predicate="HAS_SECTION", to_node_id=section_node.id))
                chunk_id = new_id()
                digest = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
                db.add(KnowledgeChunk(
                    id=chunk_id, source_id=source.id, node_id=section_node.id,
                    path=rel, heading=chunk.heading, content=chunk.content,
                    content_hash=digest, position=chunk.position, created_at=utc_now(),
                ))
                db.execute(
                    text("INSERT INTO knowledge_chunks_fts(chunk_id, title, path, content) VALUES (:id, :title, :path, :content)"),
                    {"id": chunk_id, "title": chunk.heading, "path": rel, "content": chunk.content},
                )
                chunk_count += 1

        source.document_count = len(parsed)
        source.chunk_count = chunk_count
        source.status = "ready"
        source.updated_at = utc_now()
        result = {
            "id": source.id, "name": source.name, "revision": source.revision,
            "license": source.license, "documents": source.document_count, "chunks": source.chunk_count,
        }
    return result


def list_sources() -> list[dict]:
    with database.session_scope() as db:
        rows = db.query(KnowledgeSource).order_by(KnowledgeSource.name).all()
        return [{
            "id": row.id, "name": row.name, "url": row.url, "revision": row.revision,
            "license": row.license, "status": row.status, "documents": row.document_count,
            "chunks": row.chunk_count, "updatedAt": row.updated_at,
        } for row in rows]


def _match_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_]+", query.lower())[:12]
    tokens = [t for t in tokens if len(t) > 1]
    if not tokens:
        raise ValueError("search query must contain words")
    return tokens


def search(query: str, limit: int = 8) -> list[dict]:
    """BM25 full-text search with stable, source-pinned citations."""
    limit = max(1, min(int(limit), 20))
    tokens = _match_tokens(query)
    sql = text("""
        SELECT c.id, s.name AS source_name, s.url, s.revision, c.path, c.heading,
               c.position, c.content, c.content_hash, n.properties_json, bm25(knowledge_chunks_fts, 0.0, 4.0, 2.0, 1.0) AS rank
        FROM knowledge_chunks_fts
        JOIN knowledge_chunks c ON c.id = knowledge_chunks_fts.chunk_id
        JOIN knowledge_sources s ON s.id = c.source_id
        JOIN knowledge_nodes n ON n.id = c.node_id
        WHERE knowledge_chunks_fts MATCH :match
        ORDER BY rank
        LIMIT :limit
    """)
    with database.session_scope() as db:
        # Prefer sections containing every query term; broaden only when the
        # research text does not use all of the user's words.
        strict = " AND ".join(f'"{token}"' for token in tokens)
        rows = db.execute(sql, {"match": strict, "limit": limit}).mappings().all()
        if not rows:
            broad = " OR ".join(f'"{token}"' for token in tokens)
            rows = db.execute(sql, {"match": broad, "limit": limit}).mappings().all()
    results = []
    for row in rows:
        anchor = re.sub(r"[^a-z0-9 -]", "", row["heading"].lower()).strip().replace(" ", "-")
        source_url = f"{row['url'].rstrip('/')}/blob/{quote(row['revision'], safe='')}/{quote(row['path'])}"
        if anchor:
            source_url += f"#{anchor}"
        properties = json.loads(row["properties_json"]) if isinstance(row["properties_json"], str) else (row["properties_json"] or {})
        if properties.get("kind") in {"user_note", "selected_source"}:
            source_url = f"/api/agent/knowledge/passages/{row['id']}"
        results.append({
            "id": row["id"], "source": row["source_name"], "path": row["path"],
            "heading": row["heading"], "position": row["position"], "url": source_url,
            "citation": f"[{row['source_name']}:{row['path']} — {row['heading']} | {row['id']}]",
            "content": row["content"], "contentHash": row["content_hash"], "revision": row["revision"],
            "trust": "untrusted_reference", "snapshotUrl": f"/api/agent/knowledge/passages/{row['id']}",
        })
    return results


def graph_summary() -> dict:
    with database.session_scope() as db:
        counts = dict(db.execute(text("SELECT kind, COUNT(*) FROM knowledge_nodes GROUP BY kind")).all())
        edges = dict(db.execute(text("SELECT predicate, COUNT(*) FROM knowledge_edges GROUP BY predicate")).all())
    return {"nodes": counts, "edges": edges}


def ingest_text(*, title: str, content: str, kind: str, source_url: str | None,
                author: str | None, license_name: str | None) -> dict:
    """Ingest only text explicitly supplied by the user; never fetch a URL.

    Each submission is an immutable source version. Short notes are preserved.
    Selection/ownership is user asserted, not an endorsement or license check.
    """
    from urllib.parse import urlparse

    if kind not in {"user_note", "selected_source"}:
        raise ValueError("kind must be user_note or selected_source")
    if not title.strip() or not content.strip():
        raise ValueError("title and content are required")
    if len(content.encode()) > MAX_TEXT_BYTES:
        raise ValueError("text exceeds the 2 MB ingestion limit")
    if source_url and urlparse(source_url).scheme not in {"https", "http"}:
        raise ValueError("source_url must be an http(s) attribution URL")
    if kind == "selected_source" and not source_url:
        raise ValueError("selected source requires an attribution URL")
    sid = new_id()
    revision = hashlib.sha256(content.encode()).hexdigest()
    # Submission IDs avoid conflating revisions, equal titles, or user notes
    # with an existing curated repository's name.
    name = f"{title.strip()[:220]} [{sid}]"
    path = "submission.txt"
    with database.session_scope() as db:
        source = KnowledgeSource(id=sid, name=name, url=source_url or "", revision=revision,
                                 license=license_name, status="ready", document_count=1, chunk_count=0)
        db.add(source)
        db.flush()
        root = _node(db, sid, kind, "submission", title, {
            "kind": kind, "author": author, "sourceUrl": source_url, "submittedAt": utc_now(),
            "trust": "untrusted_user_supplied_reference", "revision": revision,
            "license": license_name, "fullText": content,
        })
        for position, piece in enumerate(_split_large(content)):
            node = _node(db, sid, "section", f"passage:{position}", title,
                         {"kind": kind, "position": position, "sourceUrl": source_url})
            db.add(KnowledgeEdge(from_node_id=root.id, predicate="HAS_SECTION", to_node_id=node.id))
            chunk_id = new_id()
            db.add(KnowledgeChunk(id=chunk_id, source_id=sid, node_id=node.id, path=path, heading=title,
                                 content=piece, content_hash=hashlib.sha256(piece.encode()).hexdigest(), position=position))
            db.execute(text("INSERT INTO knowledge_chunks_fts(chunk_id,title,path,content) VALUES (:id,:title,:path,:content)"),
                       {"id": chunk_id, "title": title, "path": path, "content": piece})
            source.chunk_count += 1
        return {"id": sid, "name": name, "revision": revision, "chunks": source.chunk_count,
                "kind": kind, "trust": "untrusted_user_supplied_reference"}


def source_provenance(db, source_id: str) -> dict:
    """Return source assertions without duplicating the submitted full text."""
    root = (db.query(KnowledgeNode).filter(KnowledgeNode.source_id == source_id,
            KnowledgeNode.kind.in_(("repository", "user_note", "selected_source"))).one_or_none())
    return {k: v for k, v in (root.properties_json or {}).items() if k != "fullText"} if root else {}


def passage(chunk_id: str) -> dict:
    with database.session_scope() as db:
        chunk = db.get(KnowledgeChunk, chunk_id)
        if not chunk:
            raise ValueError("passage unavailable")
        source = db.get(KnowledgeSource, chunk.source_id)
        node = db.get(KnowledgeNode, chunk.node_id)
        return {"id": chunk.id, "content": chunk.content, "contentHash": chunk.content_hash,
                "position": chunk.position, "sourceId": source.id, "source": source.name,
                "revision": source.revision, "sourceUrl": source.url or None,
                "path": chunk.path, "heading": chunk.heading,
                "provenance": {**source_provenance(db, source.id), **(node.properties_json or {})},
                "trust": "untrusted_reference", "snapshotUrl": f"/api/agent/knowledge/passages/{chunk.id}"}
