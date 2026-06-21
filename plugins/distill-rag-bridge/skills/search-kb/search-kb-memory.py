#!/usr/bin/env python3
"""Search the knowledgebase vector database semantically.

Supports context-aware re-ranking via --context flag.
Context can be inline text or @filepath (reads file contents).
Handles both OKF (.md) and legacy (.yaml) knowledgebase entries.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

# Add _common directory to path (relative to this script's location)
_COMMON = Path(__file__).resolve().parent.parent.parent / "_common"
sys.path.insert(0, str(_COMMON))

from kb_common import embed_cached, unpack_vector, cosine

# --- Stopwords for term extraction ---
_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "been", "were", "they",
    "will", "what", "when", "where", "which", "their", "there",
    "would", "could", "should", "about", "into", "than", "then",
    "also", "just", "more", "some", "them", "each", "other",
    "file", "line", "text", "data", "code", "path", "name", "type",
})

DB_PATH = Path("/project/.agent/agentdb.sqlite3")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- Context-aware ranking helpers ---


def load_context(value: str) -> str:
    """Load context from inline text or @filepath.

    If value starts with '@', the rest is treated as a file path
    and its contents are read. Otherwise value is returned as-is.
    """
    if value.startswith("@"):
        path = Path(value[1:])
        if not path.exists():
            print(f"Error: context file not found: {path}", file=sys.stderr)
            sys.exit(1)
        return path.read_text()
    return value


def extract_terms(text: str) -> set[str]:
    """Extract significant terms from context text for exact-match boosting.

    Splits on word boundaries, keeps alphanumeric tokens with underscores,
    dots, dashes (identifiers, paths, error codes). Filters to len >= 4
    and excludes stopwords.
    """
    terms = set()
    for token in re.findall(r"[A-Za-z0-9_.-]+", text):
        token = token.strip("._-")
        if len(token) >= 4 and token.lower() not in _STOPWORDS:
            terms.add(token)
    return terms


def term_boost(terms: set[str], content: str) -> float:
    """Compute a gentle score boost based on exact term matches in content.

    Returns 0.0-0.1 additive boost. Max boost at 3+ matched terms.
    Matching is case-insensitive.
    """
    if not terms:
        return 0.0
    lower_content = content.lower()
    matched = sum(1 for t in terms if t.lower() in lower_content)
    return min(1.0, matched / 3) * 0.1


# --- OKF metadata extraction ---


def extract_okf_metadata(meta: dict) -> dict:
    """Extract OKF-specific metadata from the stored metadata dict."""
    return {
        "type": meta.get("type") or meta.get("okf_type", ""),
        "tags": meta.get("tags", []),
        "description": meta.get("description", ""),
        "timestamp": meta.get("timestamp", ""),
    }


# --- Main search logic ---


def search(query: str, namespace: str | None = None, limit: int = 5,
           context: str | None = None):
    db = sqlite3.connect(str(DB_PATH))
    qvec = embed_cached(query)

    # Embed context if provided (cached, ~0ms on repeat)
    cvec = None
    ctx_terms = None
    if context:
        cvec = embed_cached(context[:512])
        ctx_terms = extract_terms(context)

    where = "WHERE namespace = ?" if namespace else ""
    params = (namespace,) if namespace else ()
    rows = db.execute(
        f"SELECT key, namespace, content, metadata_json, vector "
        f"FROM embeddings {where}", params
    ).fetchall()

    ALPHA = 0.7
    BETA = 0.3

    scored = []
    for key, ns, content, meta_json, vec_blob in rows:
        doc_vec = unpack_vector(vec_blob)
        base = cosine(qvec, doc_vec)  # query similarity

        if cvec is not None:
            base = ALPHA * base + BETA * cosine(cvec, doc_vec)  # interpolation

        if ctx_terms:
            base = base + term_boost(ctx_terms, content)  # gentle nudge

        scored.append((base, key, ns, content, json.loads(meta_json)))

    scored.sort(key=lambda x: x[0], reverse=True)

    print(f"Search: \"{query}\"")
    if namespace:
        print(f"  Namespace: {namespace}")
    if context:
        ctx_preview = context[:60].replace("\n", "\\n")
        print(f"  Context: \"{ctx_preview}...\" ({len(context)} chars)")
    print(f"  Results: {len(scored)} candidates, top {limit}:\n")

    for i, (score, key, ns, content, meta) in enumerate(scored[:limit]):
        title = meta.get("title", key)
        okf_meta = extract_okf_metadata(meta)
        okf_type = okf_meta.get("type") or ns
        okf_tags = okf_meta.get("tags", [])
        tag_str = f" [{', '.join(okf_tags[:3])}]" if okf_tags else ""

        print(f"  {i + 1}. [{okf_type}]{tag_str} {title}  (score: {score:.4f})")
        if okf_meta.get("description"):
            print(f"     {okf_meta['description'][:120]}")
        else:
            print(f"     {content[:120]}...")
        print()

    db.close()


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Search knowledgebase with optional context-aware re-ranking."
    )
    p.add_argument("query", nargs="+",
                   help="Natural language query")
    p.add_argument("-n", "--namespace",
                   help="Filter to namespace (decisions, patterns, sessions)")
    p.add_argument("-l", "--limit", type=int, default=5,
                   help="Number of results (default: 5)")
    p.add_argument("--context",
                   help="Context text or @filepath for re-ranking. "
                        "Prefix with @ to read from a file.")
    args = p.parse_args()

    ctx = load_context(args.context) if args.context else None
    search(" ".join(args.query), args.namespace, args.limit, ctx)
