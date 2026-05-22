#!/usr/bin/env python3
"""
Shared utilities for the KB embedding pipeline.
Provides embedding with content-addressable caching (SHA-256 → SQLite),
plus vector packing/unpacking and cosine similarity.

Used by: load-kb-to-memory.py, search-kb-memory.py

Embedding fallback chain (all use bge-large, 1024-dim):
  1. Central KB embed-server HTTP sidecar (~100ms)
  2. Ollama bge-large:latest (~330ms, auto-pulls if needed)
"""
import hashlib
import json
import os
import sqlite3
import struct
import sys
import urllib.request
from pathlib import Path

DB_PATH = Path("/project/.agent/agentdb.sqlite3")
EMBED_CACHE_DB = Path("/project/.agent/embed_cache.sqlite3")
EMBED_MODEL = "bge-large:latest"  # Must match BAAI/bge-large-en-v1.5 (1024-dim)

# Auto-create .agent/ directory on import so first-run never fails
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
EMBED_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)

_EMBED_HTTP_URL = None


def _detect_embed_http_url() -> str | None:
    """Auto-detect the Central KB embed-server HTTP endpoint (memoized)."""
    global _EMBED_HTTP_URL
    if _EMBED_HTTP_URL is not None:
        return _EMBED_HTTP_URL
    for host in ["host.containers.internal", "host.docker.internal"]:
        url = f"http://{host}:9001"
        try:
            req = urllib.request.Request(f"{url}/health", method="GET")
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read())
            if data.get("model_ready"):
                _EMBED_HTTP_URL = url
                return url
        except Exception:
            continue
    return None


def embed_http(text: str) -> list[float] | None:
    """Try the Central KB embed-server HTTP sidecar — ~100ms."""
    url = _detect_embed_http_url()
    if not url:
        return None
    try:
        payload = json.dumps({"text": text[:512]}).encode("utf-8")
        req = urllib.request.Request(
            f"{url}/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if "error" in result:
            return None
        return result["embedding"]
    except Exception:
        return None


def embed_ollama(text: str) -> list[float] | None:
    """Try Ollama embedding API — ~330ms, auto-pulls model if needed."""
    model = os.environ.get("KB_EMBED_MODEL", EMBED_MODEL)
    payload = json.dumps({"model": model, "prompt": text[:512]}).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:11434/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        result = json.loads(resp.read())
        vec = result.get("embedding")
        if vec and len(vec) == 1024:
            return vec
        return None
    except Exception:
        return None


def embed(text: str) -> list[float]:
    """Generate 1024-dim embedding — tries HTTP sidecar, then Ollama.

    Both sources use BAAI/bge-large-en-v1.5 (1024-dim) so vectors are
    compatible across local and central KB indexes.
    """
    emb = embed_http(text)
    if emb is not None:
        return emb
    emb = embed_ollama(text)
    if emb is not None:
        return emb
    print("ERROR: No embedding source available.", file=sys.stderr)
    print("  Tried: 1) embed-server HTTP (host.containers.internal:9001)", file=sys.stderr)
    print(f"         2) Ollama (localhost:11434, model={EMBED_MODEL})", file=sys.stderr)
    print("  Fix: start central-kb embed-server OR pull: ollama pull bge-large:latest", file=sys.stderr)
    sys.exit(1)


def embed_cached(text: str, label: str = "") -> list[float]:
    """Generate embedding with content-addressable caching.

    Computes SHA-256 of the text and checks the embed_cache table in
    embed_cache.sqlite3 (separate DB to avoid connection contention with
    agentdb.sqlite3). On cache hit, returns stored vector (~0ms).
    On cache miss, calls embed(), stores result, then returns it.

    Args:
        text: Text to embed (first 512 chars used).
        label: Optional label for progress messages (e.g. filename).
    """
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    db = sqlite3.connect(str(EMBED_CACHE_DB))
    db.execute("PRAGMA journal_mode=WAL")
    init_cache_table(db)
    row = db.execute(
        "SELECT vector FROM embed_cache WHERE content_hash = ?", (h,)
    ).fetchone()
    if row is not None:
        vec = unpack_vector(row[0])
        db.close()
        return vec
    if label:
        print(f"    [embed] {label}...", end="", flush=True)
    vec = embed(text)
    if label:
        print(" done", flush=True)
    db.execute(
        "INSERT OR REPLACE INTO embed_cache (content_hash, vector, created_at) "
        "VALUES (?, ?, datetime('now'))",
        (h, pack_vector(vec)),
    )
    db.commit()
    db.close()
    return vec


def init_cache_table(db: sqlite3.Connection):
    """Ensure the embed_cache table exists (idempotent)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS embed_cache (
            content_hash TEXT PRIMARY KEY,
            vector BLOB NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)


def pack_vector(vec: list[float]) -> bytes:
    """Pack float list into compact binary for SQLite BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Unpack binary BLOB back into float list."""
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0