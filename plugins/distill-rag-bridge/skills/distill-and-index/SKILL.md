---
name: distill-and-index
description: Distill conversation insights into durable knowledgebase files, then index them for search (vector DB and Central KB).
allowed-tools: Bash Read Write Edit
---

# Distill & Index

Extract high-value information from a conversation and persist it so future sessions pick up where this one left off. Knowledgebase files are indexed for search using the best available method.

## Two-Tier Indexing

This skill detects which indexing systems are available and uses **all that are present**:

| Mode | Scope | Detection | Index Method | Search Method |
|------|-------|-----------|-------------|----------------|
| **Vector DB** | Local | embed-server or Ollama available + `bge-large` model | `load-kb-to-memory.py` (cosine similarity over embeddings) | `search-kb` skill |
| **Central KB** | Shared | `kb` CLI on PATH + `kb health` succeeds | `kb submit` (1024-dim embeddings, auto-detected server URL) | `search-kb` skill |

**Local vs Shared:** Vector DB is a **local** index — knowledge stays in this project. Central KB is a **shared** index — knowledge is pushed to a server where other projects and sessions can discover it. Both can run in parallel.

**Why Central KB matters:** It enables cross-project knowledge sharing. Decisions, patterns, and troubleshooting procedures from one project become searchable by any project on the same Central KB server.

## Embedding Strategy

All indexing requires 1024-dim embeddings (`bge-large-en-v1.5`). The embedding source is detected in priority order:

| Priority | Source | Speed | How |
|-----------|--------|-------|-----|
| 1 | **embed-server** (local socket daemon) | ~40ms | Unix socket at `/tmp/embed-server.sock`, uses `sentence-transformers` |
| 2 | **embed-server** (Central KB sidecar, HTTP) | ~100ms | HTTP at `host.containers.internal:9001`, `POST /embed {"text":"..."}` |
| 3 | **Ollama** (fallback) | ~330ms | HTTP at `localhost:11434/api/embeddings`, model `bge-large:latest` |

- `load-kb-to-memory.py` and `search-kb-memory.py` try embed-server socket → Ollama
- `kb submit` uses Ollama (the Central KB server uses its own embed-server sidecar for search-time queries)
- **Never mix embedding dimensions** — all entries must be 1024-dim

## Platform Behavior

| Platform | Memory files | Knowledgebase files | Index |
|----------|-------------|---------------------|-------|
| **Pi** | ❌ Skipped — handled by `pi-hermes-memory` | ✅ decisions, patterns, sessions | ✅ vector DB, Central KB |
| **Claude Code** | ✅ `~/.claude/projects/*/memory/` | ✅ decisions, patterns, sessions | ✅ vector DB, Central KB |

**On Pi**, do NOT write memory entries (`MEMORY.md`, `USER.md`, etc.). The `pi-hermes-memory` extension already manages all memory — writing duplicate entries causes conflicts. Focus exclusively on knowledgebase distillation and indexing.

**On Claude Code**, write both memory and knowledgebase files as described in Phase 1.

## When to Use

- Ending a long or significant session
- After completing a milestone or phase
- Before context compaction
- The user explicitly asks to save insights for future sessions

## Pre-flight: Detect Index Mode

Before Phase 2, determine which indexing methods are available:

```bash
INDEX_MODES=[]

# Check for vector DB — embed-server (socket or HTTP) or Ollama
HAS_EMBED=false
if [ -S /tmp/embed-server.sock ]; then
  HAS_EMBED=true
  INDEX_MODES+=("vectordb")
  echo "Embedding: embed-server socket (~40ms)"
elif curl -sf http://host.containers.internal:9001/health 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
sys.exit(0 if d.get('model_ready') else 1)" 2>/dev/null; then
  HAS_EMBED=true
  INDEX_MODES+=("vectordb")
  echo "Embedding: embed-server HTTP (~100ms)"
elif curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
  HAS_EMBED=true
  INDEX_MODES+=("vectordb")
  echo "Embedding: Ollama (~330ms)"
fi

# Check for Central KB (shared index)
# - kb search/pull: only needs server healthy (query vectors generated server-side)
# - kb submit: also needs client-side embedding source (vectors generated before sending)
if command -v kb &>/dev/null && kb health &>/dev/null; then
  if [ "$HAS_EMBED" = true ]; then
    INDEX_MODES+=("central-kb")
  else
    echo "⚠ Central KB: server reachable for search/pull, but no client-side embedding source — submit skipped"
    echo "  kb submit needs client-side embeddings. Fix: start embed-server or ollama pull bge-large:latest"
    echo "  kb search and kb pull still work (server generates query vectors)"
  fi
fi

if [ ${#INDEX_MODES[@]} -eq 0 ]; then
  echo "INDEX_MODE=none"
  echo "⚠ No indexer available. Run Phase 1 (distill) only."
  echo "  To enable indexing: start embed-server, or pull Ollama model:"
  echo "    ollama pull bge-large:latest"
else
  echo "INDEX_MODES=${INDEX_MODES[*]}"
fi
```

- If `vectordb`: run `load-kb-to-memory.py` — embeds entries and stores in local SQLite
- If `central-kb`: run `kb submit` — pushes entries to shared Central KB server (auto-embeds with bge-large 1024-dim)
- Both can be active simultaneously — they serve different search needs
- If `none`: skip Phase 2. Files are written to `knowledgebase/` and will be indexed on the next successful run. To fix:
  ```bash
  # Option 1: Start the embed-server daemon (fastest, ~40ms)
  python3 /project/tooling/scripts/embed-server.py &

  # Option 2: Start Ollama + pull model (works everywhere, ~330ms)
  ollama serve &
  ollama pull bge-large:latest
  ```
  Note: `kb search`, `kb explain`, and `kb pull` still work in Phase 3 even without a local embedding source — the Central KB server generates query vectors server-side. Only `kb submit` and local vector DB need client-side embeddings.

## Architecture

```
Conversation ──► Phase 1 (Distill) ──► knowledgebase/*.yaml
                     │                        │
                     │  (Pi: skip memory)     ▼
                     │              Phase 2 (Index) — all available run in parallel
                     │             ┌─────────────────────┐
                     │             │ detect available     │
                     │             │ indexers & embedders│
                     │             └───┬──────────┬───────┘
                     │            vectordb   central-kb
                     │                │           │
                     │                ▼           ▼
                     │        load-kb-to-    kb submit
                     │        memory.py     (1024-dim)
                     │             │           │
                     │             ▼           ▼
                     │       agentdb.       Central KB
                     │       sqlite3         server
                     │       (local)      (shared,
                     │                     cross-project)
                     │          │           │
                     │          ▼           ▼
                     │    search-kb    (unified skill)
                     │    skill        searches all backends
                     │                   kb pull/drift
                     │
                     ▼
            (Claude only) memory/*.md
```

## Prerequisites

- `session-distillation` skill installed
- **For vector DB:** embed-server running (`/tmp/embed-server.sock`) OR Ollama running with `bge-large:latest` pulled — embed-server is ~8× faster and preferred
- **For Central KB:** `kb` CLI installed (`kb` skill) + server reachable + Ollama with `bge-large:latest` for embedding generation
- Scripts at `/project/scripts/{load-kb-to-memory,search-kb-memory}.py` (only needed for vector DB)
- (Pi only) `pi-hermes-memory` extension installed — manages all memory file writing

## Phase 1 — Distill (always runs)

### On Pi

Run the session-distillation workflow for **knowledgebase files only** (skip memory):

1. **Scan** the conversation for decisions, gotchas, architecture realities, user preferences, bug root causes, integration details, troubleshooting procedures, and operational risks
2. **Check existing entries** — read `knowledgebase/index.yaml` before writing
3. **Write knowledge base entries** — YAML files for decisions, patterns, and sessions:
   - `knowledgebase/decisions/*.yaml` — architecture decisions with rationale and alternatives
   - `knowledgebase/patterns/*.yaml` — implementation patterns, troubleshooting procedures
   - `knowledgebase/sessions/*.yaml` — session summaries (what was done, what changed)
4. **Update index file** — `knowledgebase/index.yaml`
5. **Verify** — no duplicates, no stale entries, index counts accurate

**Do NOT write memory files.** Pi's `pi-hermes-memory` extension handles `MEMORY.md`, `USER.md`, and failure tracking automatically. Writing memory here creates duplicate/conflicting entries.

### On Claude Code

Run the full session-distillation workflow including both memory and knowledgebase:

1. **Scan** the conversation (same as Pi)
2. **Check existing entries** — read `~/.claude/projects/*/memory/MEMORY.md` and `knowledgebase/index.yaml` before writing
3. **Write memory entries** — markdown files with YAML frontmatter:
   ```markdown
   ---
   name: descriptive-name
   description: one-line summary
   type: user | feedback | project | reference
   ---
   Content...
   ```
4. **Write knowledge base entries** — same as Pi above
5. **Update index files** — `MEMORY.md` and `index.yaml`
6. **Verify** — no duplicates, no stale entries, index counts accurate

## Phase 2 — Index

All detected indexers run. Vector DB (local) and Central KB (shared) are independent — each serves different search needs.

### Vector DB (local)

If an embedding source is available (embed-server socket or Ollama), build the vector index:

```bash
python3 /project/scripts/load-kb-to-memory.py
```

This reads all `knowledgebase/{decisions,patterns,sessions}/*.yaml` files, generates 1024-dim embeddings (embed-server socket preferred, Ollama fallback), and stores them in `/project/.claude/agentdb.sqlite3`. Uses `INSERT OR REPLACE` — safe to run repeatedly.

**Do not proceed** if no embedding source is available. Knowledgebase files are already written — they will be indexed on the next successful run.

Verify after indexing:

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.claude/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
"
```

### Central KB (shared, cross-project)

If the `kb` CLI is available and the server is healthy, submit entries to the Central KB:

```bash
kb submit --project $CENTRAL_KB_PROJECT
```

The `kb` CLI:
- Auto-generates 1024-dim embeddings via Ollama `bge-large:latest`
- Pre-computes simhash to avoid server-side OverflowError (unsigned int64 → signed int64 conversion)
- Submits in batches of 5
- Reports accepted/duplicate/conflicted/error for each entry
- Server URL is auto-detected (host.containers.internal:9000) or from `CENTRAL_KB_URL` env var

**Prerequisites:** `CENTRAL_KB_PROJECT` env var must be set. If not set, Central KB indexing is skipped with a warning.

Verify after submitting:

```bash
kb health                    # Server reachable
kb search "test" --scope $CENTRAL_KB_PROJECT  # Search works
kb pull --project $CENTRAL_KB_PROJECT       # Pull works
kb explain "topic" --scope $CENTRAL_KB_PROJECT  # Structured results → agent synthesizes
```

## Phase 3 — Search

Use the **`search-kb` skill** — it searches all available backends and the agent synthesizes results into a coherent narrative.

- **Vector DB (local):** cosine similarity search via `search-kb-memory.py`
- **Central KB (shared):** semantic + FTS search via `kb search`, structured explain via `kb explain`
- Both backends are searched when available — they cover different scopes (local vs cross-project)
- The agent synthesizes findings from all backends into a unified answer

See the `search-kb` skill for full details, pre-flight detection, and agent patterns.

### Quick reference

| Question | Command |
|----------|--------|
| Local search (all namespaces) | `python3 /project/scripts/search-kb-memory.py "<query>"` |
| Local search (decisions only) | `python3 /project/scripts/search-kb-memory.py "<query>" -n decisions` |
| Shared search | `kb search "<query>" --scope <project>` |
| Structured explain | `kb explain "<query>" --scope <project>` |
| Pull new entries from other projects | `kb pull --project <project>` |
| Check for concept drift | `kb drift --project <project>` |

## How Agents Use This

Agents treat the distill-and-index pipeline as a two-way memory system:

### Writing (Phase 1 → 2)

**On Pi (vector DB + Central KB):**
```
Agent completes work
  → distill-and-index runs (manual or PreCompact hook)
    → Phase 1: session-distillation scans conversation, writes KB files only
               (memory is skipped — pi-hermes-memory handles that independently)
    → Phase 2a: load-kb-to-memory.py indexes entries into local vector DB
    → Phase 2b: kb submit pushes entries to Central KB (cross-project sharing)
      → Local knowledge searchable via /search-kb or search-kb-memory.py
      → Shared knowledge searchable via kb search/explain
```

**On Claude Code:** Same flow, but Phase 1 also writes memory files. Central KB push still runs in Phase 2b.

### Reading (Phase 3)

**Local search (vector DB):**
```
Agent starts new task
  → search-kb-memory.py "<topic>" — find relevant prior knowledge by similarity
```

**Shared search (Central KB):**
```
Agent starts new task
  → kb search "topic" --scope my-project — search across project entries
  → kb explain "topic" --scope my-project — structured view of how entries relate
  → Agent synthesizes the narrative from kb explain output (no --llm needed in-session)
  → kb pull --project my-project — pull new entries from server
  → kb drift --project my-project — check for concept drift
  → Cross-project: find decisions/patterns from other teams
```

**Important:** When an agent is in-session, `kb explain` (without `--llm`) provides structured output that the agent LLM itself synthesizes into a narrative. This is superior to `kb explain --llm` which calls a small local model — the session model is far more capable. Use `--llm` only for standalone CLI use outside an agent session.

### Concrete agent patterns

**Before implementing a feature:**
1. Local: `search-kb-memory.py "feature architecture"` — find related decisions
2. Shared: `kb search "feature architecture" --scope my-project` — find cross-project knowledge
3. Apply known constraints, avoid rejected alternatives

**When debugging a problem:**
1. Local: `search-kb-memory.py "<error message>"` — find past encounters
2. Shared: `kb search "error symptom" --scope my-project` — cross-project troubleshooting
3. Shared: `kb explain "error symptom" --scope my-project` — structured view of how entries relate, then agent synthesizes narrative
4. Trace dependency chains, look for patterns

**When a session ends (PreCompact hook):**
1. Distill findings into knowledgebase (skip memory on Pi)
2. Index local: `load-kb-to-memory.py` (if embedding source available)
3. Index shared: `kb submit --project $CENTRAL_KB_PROJECT` (if Central KB available)
4. Next session picks up from where this one left off

## Auto-Run via Hook

### On Pi — standard mode

For automatic distillation before context compaction, add to `.pi/settings.local.json`:

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into knowledgebase files using session-distillation (skip memory — hermes-memory handles that). Phase 2: run python3 /project/scripts/load-kb-to-memory.py to index KB files into the vector database, then kb submit --project $CENTRAL_KB_PROJECT to push entries to Central KB (if kb CLI available). Verify entry counts and kb submit results.",
        "statusMessage": "Distilling session, indexing into vector DB, and syncing to Central KB..."
      }]
    }]
  }
}
```

### On Claude Code

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into memory/KB files using session-distillation. Phase 2: run python3 /project/scripts/load-kb-to-memory.py to index KB files into the vector database, then kb submit --project $CENTRAL_KB_PROJECT to push entries to Central KB (if kb CLI available). Verify entry counts and kb submit results.",
        "statusMessage": "Distilling session, indexing into vector DB, and syncing to Central KB..."
      }]
    }]
  }
}
```

## Output

After running, confirm:

**All modes (Pi):**
1. **KB entries created** — `cat knowledgebase/index.yaml`
2. **Memory untouched** — hermes-memory manages memory files independently

**All modes (Claude Code):**
1. **Memory files written** — `ls ~/.claude/projects/*/memory/`
2. **MEMORY.md updated** — `cat ~/.claude/projects/*/memory/MEMORY.md`
3. **KB entries created** — `cat knowledgebase/index.yaml`

**Vector DB (if available):**
1. **Vector index populated** — `python3 -c "import sqlite3; db=sqlite3.connect('/project/.claude/agentdb.sqlite3'); print(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 'entries')"`
2. **Search works** — `python3 /project/scripts/search-kb-memory.py "test" -l 3`

**Central KB (if available):**
1. **Server healthy** — `kb health`
2. **Entries submitted** — check kb submit output for accepted count
3. **Search works** — `kb search "test" --scope $CENTRAL_KB_PROJECT`