---
name: search-kb
description: Search knowledgebase for relevant prior knowledge. Searches all available backends — local vector DB and Central KB (shared). Finds decisions, patterns, and sessions before starting new work. Agent synthesizes results into a narrative.
allowed-tools: Bash
---

# Search Knowledgebase

Search the knowledgebase for decisions, patterns, and sessions relevant to a natural language query. Automatically searches **all available backends** and synthesizes the results.

## Two-Tier Search

| Tier | Scope | Detection | Search Method | Embeddings needed |
|------|-------|-----------|--------------|-------------------|
| **Vector DB** | Local (this project) | embed-server or Ollama available + `agentdb.sqlite3` exists | `search-kb-memory.py` (cosine similarity) | Yes (client-side, for query vector) |
| **Central KB** | Shared (cross-project) | `kb` CLI on PATH + `kb health` succeeds | `kb search`, `kb explain` | No (server generates query vectors) |

Both tiers search independently and return complementary results. The agent synthesizes findings from all available backends into a coherent narrative.

**Local vs Shared:** Vector DB contains only entries **submitted from this project**. Central KB contains entries from **all projects** on the same server — useful for finding cross-project decisions, shared patterns, and troubleshooting procedures solved by other teams.

## Pre-flight: Detect Available Backends

Before searching, determine which backends are available:

```bash
SEARCH_MODES=()

# Check for local vector DB
HAS_EMBED=false
if [ -S /tmp/embed-server.sock ]; then
  HAS_EMBED=true
elif curl -sf http://host.containers.internal:9001/health 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
sys.exit(0 if d.get('model_ready') else 1)" 2>/dev/null; then
  HAS_EMBED=true
elif curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
  HAS_EMBED=true
fi

if [ "$HAS_EMBED" = true ] && [ -f /project/.agent/agentdb.sqlite3 ]; then
  SEARCH_MODES+=("vectordb")
fi

# Check for Central KB (no client-side embeddings needed for search)
if command -v kb &>/dev/null && kb health &>/dev/null; then
  SEARCH_MODES+=("central-kb")
fi

if [ ${#SEARCH_MODES[@]} -eq 0 ]; then
  echo "SEARCH_MODES=none"
  echo "⚠ No search backend available."
else
  echo "SEARCH_MODES=${SEARCH_MODES[*]}"
fi
```

## Search Procedure

Always search **all available backends**. Do not skip one just because the other returns results — they cover different scopes.

### Step 1: Search local vector DB (if available)

```bash
# Basic search (unchanged)
python3 /project/scripts/search-kb-memory.py "<query>" [-n namespace] [-l limit]

# Context-aware search — pass code snippet, error message, or file contents
# to re-rank results toward the current work context
python3 /project/scripts/search-kb-memory.py "<query>" --context "<inline text>"
python3 /project/scripts/search-kb-memory.py "<query>" --context @<filepath>

# Examples:
python3 /project/scripts/search-kb-memory.py "port" --context "$(cat docker-compose.yml)"
python3 /project/scripts/search-kb-memory.py "connection refused" --context @error.log
python3 /project/scripts/search-kb-memory.py "model" --context "$(cat tooling/scripts/kb_common.py)"
```

| Namespace | Content | Use When |
|-----------|---------|----------|
| `decisions` | Architecture decisions with rationale and alternatives | Making design choices |
| `patterns` | Implementation patterns, troubleshooting procedures | Solving a specific problem |
| `sessions` | Session summaries (what was done, what changed) | Finding recent related work |

Omit `-n` to search all namespaces. Use `-l` to control result count (default 5).

The `--context` flag accepts either inline text (shell-quoted) or a `@`-prefixed file path.
Context length is capped at 512 chars (embed-server limit).

### Step 2: Search Central KB (if available)

```bash
# Quick search — returns ranked entries
kb search "<query>" --scope <project>

# Structured explain — returns entries with scores and excerpts (agent synthesizes narrative)
kb explain "<query>" --scope <project>
```

Central KB search is **free** — no local embeddings needed. The server generates query vectors via its own embed-server sidecar.

### Step 3: Agent synthesizes

After gathering results from all available backends, synthesize a coherent narrative that:
- Identifies the most relevant entries across both tiers
- Traces how decisions evolved over time (dates, superseding entries)
- Highlights cross-project insights (Central KB only)
- Notes any conflicting information between local and shared entries
- Answers the original query with specific entry references

**In an agent session, do NOT use `kb explain --llm`.** The agent itself is the LLM — it produces far better syntheses than any local Ollama model. Read the structured output and synthesize yourself.

## Embedding Source Fallback Chain

When client-side embeddings are needed (vector DB search), sources are tried in order:

| Priority | Source | Speed | How |
|-----------|--------|-------|-----|
| 1 | embed-server (local socket) | ~40ms | `/tmp/embed-server.sock`, uses `sentence-transformers` |
| 2 | embed-server (Central KB sidecar, HTTP) | ~100ms | `host.containers.internal:9001`, `POST /embed {"text":"..."}` |
| 3 | Ollama (fallback) | ~330ms | `localhost:11434/api/embeddings`, model `bge-large:latest` |

If no source is available, vector DB search is unavailable. Central KB search still works.

## Context-Aware Search (`--context`)

When you pass `--context`, the search uses two mechanisms to re-rank results:

1. **Embedding interpolation (primary):** The context is embedded into a 1024-dim vector.
   The final score is `0.7 * cos(query, doc) + 0.3 * cos(context, doc)`.
   This biases results toward the semantic domain of the context.

2. **Term boost (secondary):** Identifiers (≥4 chars, not stopwords) are extracted from
   the context. Documents containing exact matches get a gentle score nudge of up to
   +0.1 (additive). This helps exact error codes, function names, or file paths rank
   higher even if their semantic vector is distant.

### When to Use `--context`

| Situation | Context to Pass | Effect |
|-----------|----------------|--------|
| **Debugging an error** | Error message / stack trace | Biases toward entries with similar error patterns |
| **Reading a file** | File contents (YAML, Python, config) | Biases toward entries in that domain |
| **Code review** | The diff or file under review | Finds relevant patterns and decisions |
| **Architecture question** | Current design doc section | Finds related decisions and rejected alternatives |

### Examples

```bash
# Debugging — pass the error message
python3 /project/scripts/search-kb-memory.py "timeout" --context @/tmp/last-error.log

# Architecture — pass the file you're working on
python3 /project/scripts/search-kb-memory.py "port" --context @docker-compose.yml

# Reading code — pass the code snippet
python3 /project/scripts/search-kb-memory.py "embedding" --context "$(cat tooling/scripts/kb_common.py | head -30)"
```

Context is cached via SHA-256 content hash — passing the same context repeatedly
costs ~0ms (cache hit).

| Situation | Primary | Secondary |
|-----------|---------|-----------|
| **Before implementing** | Local: find related decisions, pass context of file you're changing with `--context @file` | Central KB: find cross-project knowledge |
| **When debugging** | Local: pass the error message or log as `--context @error.log` | Central KB: find solutions from other projects |
| **Before architecture decisions** | Local: find prior choices + rejected alternatives, pass design doc as context | Central KB: find what other teams decided |
| **Cross-project question** | Central KB: search across scopes | Local: check for local context |
| **After long session** | Not search — run `distill-and-index` instead | |

## Agent Patterns

**Before starting implementation:**
1. Search local with context: `search-kb-memory.py "<feature> architecture" --context @current-file -n decisions`
2. Search shared: `kb search "<feature> architecture" --scope <project>`
3. Explain shared: `kb explain "<feature> architecture" --scope <project>`
4. Synthesize: combine local constraints + cross-project insights → informed approach

**When debugging:**
1. Search local with error context: `search-kb-memory.py "<error message>" --context @error.log`
2. Search shared: `kb search "<error symptom>" --scope <project>`
3. Explain shared: `kb explain "<error symptom>" --scope <project>`
4. Synthesize: trace from symptom → root cause using entries from both tiers

**Before proposing architecture:**
1. Search local with context: `search-kb-memory.py "<design question>" --context @design-doc -n decisions -l 10`
2. Search shared: `kb explain "<design concept>" --scope <project>`
3. Synthesize: what was already decided, what was rejected, what other teams chose

**Starting a new session (catch-up):**
1. Pull latest: `kb pull --project <project>`
2. Search shared: `kb explain "latest changes" --scope <project>`
3. Search local: `search-kb-memory.py "in-progress work" -n sessions`
4. Synthesize: where the last session left off, what's still open

## No Backends Available

If pre-flight returns `SEARCH_MODES=none`:
- Central KB search works without client-side embeddings — the server handles query vectors
- If Central KB is also unreachable, no automated search is possible
- Fall back to manual: `grep -r "topic" /project/knowledgebase/`
- Or fix: start embed-server, pull Ollama model, or start Central KB server