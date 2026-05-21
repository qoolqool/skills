#!/usr/bin/env python3
"""
Load knowledgebase YAML files into a persistent vector database.
Uses embed-server (sentence-transformers) for embeddings + SQLite for storage.
Embedding source: local Unix socket (/tmp/embed-server.sock) or HTTP sidecar.
"""
import json
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
        files = sorted(dir_path.glob("*.yaml"))
        print(f"  {folder}/: {len(files)} files")

        for fpath in files:
            try:
                text = fpath.read_text()
                parsed = parse_yaml_simple(text)
                entry_key = fpath.stem
                title = parsed.get("title") or parsed.get("name") or parsed.get("id") or entry_key
                desc = parsed.get("description") or parsed.get("summary") or parsed.get("decision") or text[:200]

                metadata = {
                    "title": title,
                    "type": folder,
                    "file": fpath.name,
                    "source": str(fpath.relative_to(KB.parent)),
                }
                for meta_key in ("status", "date", "category", "firstSeen", "lastUpdated"):
                    if meta_key in parsed:
                        metadata[meta_key] = parsed[meta_key]

                content_full = f"{title}\n{desc}"

                # Generate embedding (with content-addressable caching)
                vec = embed_cached(content_full, label=fpath.name)
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
