---
name: search-kb
description: Semantic search over knowledgebase decisions, patterns, and sessions using vector embeddings. Finds relevant prior knowledge before starting new work.
allowed-tools: Bash
---

# Search Knowledgebase

Semantic search over the knowledgebase vector index. Finds decisions, patterns, and sessions relevant to a natural language query — no exact keyword matching needed.

## Usage

```
/search-kb "<query>"                     # search all namespaces
/search-kb "<query>" -n decisions         # scope to decisions only
/search-kb "<query>" -n patterns -l 10    # patterns, top 10 results
```

## Pre-flight: Embedding Model Check

Before searching, verify the embedding model is available:

```bash
if test -S /tmp/embed-server.sock; then
  echo "✔ embed-server daemon ready"
elif curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('all-minilm' in m for m in models) else 1)" 2>/dev/null; then
  echo "✔ Ollama fallback ready"
else
  echo "✖ No embedding model available — search cannot proceed."
  echo "Check: docker compose logs tooling | grep -E 'embed|ollama|model'"
  exit 1
fi
```

If neither the embed daemon socket nor the Ollama fallback model is available, abort the search and instruct the user to verify the container startup.

## What It Does

1. **Embeds** the query — fast path via `embed-server` daemon (~40ms), Ollama HTTP fallback (~330ms)
2. **Scores** all entries in `/project/.claude/agentdb.sqlite3` by cosine similarity
3. **Returns** the top N results with relevance scores, namespace tags, and content previews

Typical latency: 52-87ms with embed daemon running.

## Namespaces

| Namespace | Content | Use When |
|-----------|---------|----------|
| `decisions` | Architecture decisions with rationale and alternatives | Making design choices |
| `patterns` | Implementation patterns, troubleshooting procedures | Solving a specific problem |
| `sessions` | Session summaries (what was done, what changed) | Finding recent related work |

Omit `-n` to search all namespaces.

## Agent Patterns

**Before starting implementation:**
```
/search-kb "<feature name>" -n decisions
```
Find prior decisions that constrain or inform the approach.

**When debugging:**
```
/search-kb "<error message or symptom>" 
```
Find gotchas, troubleshooting patterns, and past encounters with the same issue.

**Before proposing architecture:**
```
/search-kb "<design question>" -n decisions -l 10
```
See what was already decided and why alternatives were rejected.
