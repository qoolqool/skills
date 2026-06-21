#!/usr/bin/env python3
"""
Load knowledgebase OKF markdown and legacy YAML files into a persistent vector database.
Uses bge-large (1024-dim) embeddings via HTTP sidecar or Ollama + SQLite for storage.
Embedding source: Central KB HTTP sidecar (host.containers.internal:9001) or Ollama.

Supports both OKF (.md with YAML frontmatter) and legacy (.yaml) formats.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

# Add _common directory to path (relative to this script's location)
_COMMON = Path(__file__).resolve().parent.parent.parent / "_common"
sys.path.insert(0, str(_COMMON))

from kb_common import embed_cached, pack_vector

KB = Path("/project/knowledgebase")
DB_PATH = Path("/project/.agent/agentdb.sqlite3")


def parse_yaml_simple(text: str) -> dict:
    """Parse simple YAML flat keys and block scalars."""
    result = {}
    lines = text.split("\n")
    key = None
    buf = []
    in_block = False

    for line in lines:
        if in_block:
            if line and (line[0:2] == "  " or line.strip() == "" or line.startswith("    ")):
                buf.append(line[2:] if line.startswith("  ") and not line.startswith("    ") else line)
                continue
            else:
                result[key] = "\n".join(buf).strip()
                buf = []
                in_block = False
        m = None
        for sep in [": ", ":"]:
            idx = line.find(sep)
            if idx > 0 and not line.startswith(" "):
                m = (line[:idx], line[idx + len(sep):])
                break
        if m:
            key = m[0]
            val = m[1].strip()
            if val in (">", "|"):
                in_block = True
                buf = []
            elif val:
                result[key] = val
            else:
                result[key] = ""
    if in_block and key:
        result[key] = "\n".join(buf).strip()
    return result


def parse_okf_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from OKF markdown and return as a dict.

    Returns dict with 'frontmatter' (parsed key-values) and 'body' (markdown
    after frontmatter). Uses the app.okf library if available, otherwise
    falls back to simple YAML parsing.
    """
    # Try to use the official OKF parser
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "tooling" / "central-kb"))
        from app.okf import parse_okf_markdown
        doc = parse_okf_markdown(content)
        return {
            "frontmatter": doc.frontmatter,
            "body": doc.body,
        }
    except (ImportError, Exception):
        pass

    # Fallback: simple YAML frontmatter extraction
    result = {"frontmatter": {}, "body": ""}
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if fm_match:
        result["frontmatter"] = parse_yaml_simple(fm_match.group(1))
        result["body"] = fm_match.group(2).strip()
    else:
        result["body"] = content.strip()
    return result


def parse_yaml_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from a legacy YAML file (with or without --- markers)."""
    result = {"frontmatter": {}, "body": ""}
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if fm_match:
        result["frontmatter"] = parse_yaml_simple(fm_match.group(1))
        result["body"] = fm_match.group(2).strip()
    else:
        result["frontmatter"] = parse_yaml_simple(content)
    return result


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            namespace TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            vector BLOB NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(namespace, key)
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_ns ON embeddings(namespace)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_key ON embeddings(namespace, key)")
    db.commit()
    return db


def store_all():
    print("[1/3] Initializing SQLite database...")
    db = init_db()

    namespaces = {
        "decisions": "decisions",
        "patterns": "patterns",
        "sessions": "sessions",
    }

    total = 0
    errors = 0

    for folder, ns in namespaces.items():
        dir_path = KB / folder
        # Glob both OKF (.md) and legacy (.yaml) files
        md_files = sorted(dir_path.glob("*.md"))
        yaml_files = sorted(dir_path.glob("*.yaml"))
        # Exclude reserved filenames (index.md, log.md)
        all_files = (
            [f for f in md_files if f.name not in ("index.md", "log.md")]
            + list(yaml_files)
        )
        print(f"  {folder}/: {len(all_files)} files ({len(md_files) - len([f for f in md_files if f.name in ('index.md','log.md')])} .md, {len(yaml_files)} .yaml)")

        for fpath in all_files:
            try:
                text = fpath.read_text()
                entry_key = fpath.stem

                if fpath.suffix == ".md":
                    # OKF markdown format
                    parsed = parse_okf_frontmatter(text)
                    fm = parsed["frontmatter"]
                    body = parsed["body"]
                    title = fm.get("title") or fm.get("name") or entry_key
                    desc = fm.get("description") or body[:200]
                    tags = fm.get("tags", [])
                    if isinstance(tags, str):
                        tags = [tags]
                    okf_type = fm.get("type", folder.capitalize())

                    metadata = {
                        "title": title,
                        "type": okf_type,
                        "tags": tags,
                        "file": fpath.name,
                        "source": str(fpath.relative_to(KB.parent)),
                    }
                    # Copy optional metadata fields
                    for meta_key in ("timestamp", "resource", "status", "date", "category"):
                        if meta_key in fm:
                            metadata[meta_key] = fm[meta_key]

                    # For embedding: use only the body (strip frontmatter)
                    content_for_embed = f"{title}\n{body}"
                    # For content column: store full markdown (preserves original)
                    content_full = text
                else:
                    # Legacy YAML format
                    parsed = parse_yaml_frontmatter(text)
                    fm = parsed["frontmatter"]
                    body = parsed["body"]
                    title = fm.get("title") or fm.get("name") or fm.get("id") or entry_key
                    desc = fm.get("description") or fm.get("summary") or fm.get("decision") or body or text[:200]

                    metadata = {
                        "title": title,
                        "type": folder,
                        "file": fpath.name,
                        "source": str(fpath.relative_to(KB.parent)),
                    }
                    for meta_key in ("status", "date", "category", "firstSeen", "lastUpdated"):
                        if meta_key in fm:
                            metadata[meta_key] = fm[meta_key]

                    content_for_embed = f"{title}\n{desc}"
                    content_full = text

                # Generate embedding (with content-addressable caching)
                vec = embed_cached(content_for_embed, label=fpath.name)
                vec_blob = pack_vector(vec)

                db.execute(
                    "INSERT OR REPLACE INTO embeddings (key, namespace, content, metadata_json, vector) VALUES (?, ?, ?, ?, ?)",
                    (entry_key, ns, content_full, json.dumps(metadata), vec_blob),
                )
                total += 1
                if total % 20 == 0:
                    print(f"    ... {total} entries stored")
            except Exception as e:
                print(f"    ERROR {fpath.name}: {e}")
                errors += 1

    db.commit()

    print(f"\n[2/3] Stored: {total} entries, {errors} errors")

    # Verify
    count = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    print(f"[3/3] Database: {count} rows in {DB_PATH} ({DB_PATH.stat().st_size} bytes)")

    db.close()
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(store_all())