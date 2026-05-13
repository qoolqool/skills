---
name: distill-and-index
description: Distill conversation insights into durable knowledgebase files, then index them for search. Uses graphify (preferred) or vector DB fallback.
allowed-tools: Bash Read Write Edit
---

# Distill & Index

Extract high-value information from a conversation and persist it so future sessions pick up where this one left off. Knowledgebase files are indexed for search using the best available method.

## Dual-Mode Indexing

This skill detects which indexing system is available and uses the best one:

| Mode | Detection | Index Method | Search Method |
|------|-----------|-------------|----------------|
| **Graphify** ✅ preferred | `graphify` Python package importable + `graphify-out/graph.json` exists | `/graphify --update` (structural graph with full content) | `/graphify query`, `/graphify explain`, `/graphify path` |
| **Vector DB** fallback | Ollama running + `bge-large:latest` model | `load-kb-to-memory.py` (cosine similarity over embeddings) | `/search-kb` |

**Why graphify is preferred:** It stores relationships, communities, and full document content in one self-contained file. No external model (~670 MB), no embedding server, no separate SQLite DB. Graph traversal answers questions that cosine similarity cannot.

## Platform Behavior

| Platform | Memory files | Knowledgebase files | Index |
|----------|-------------|---------------------|-------|
| **Pi** | ❌ Skipped — handled by `pi-hermes-memory` | ✅ decisions, patterns, sessions | ✅ graphify or vector DB |
| **Claude Code** | ✅ `~/.claude/projects/*/memory/` | ✅ decisions, patterns, sessions | ✅ graphify or vector DB |

**On Pi**, do NOT write memory entries (`MEMORY.md`, `USER.md`, etc.). The `pi-hermes-memory` extension already manages all memory — writing duplicate entries causes conflicts. Focus exclusively on knowledgebase distillation and indexing.

**On Claude Code**, write both memory and knowledgebase files as described in Phase 1.

## When to Use

- Ending a long or significant session
- After completing a milestone or phase
- Before context compaction
- The user explicitly asks to save insights for future sessions

## Pre-flight: Detect Index Mode

Before Phase 2, determine which indexing method to use:

```bash
# Check for graphify (preferred)
if python3 -c "import graphify" 2>/dev/null && [ -f graphify-out/graph.json ]; then
  echo "INDEX_MODE=graphify"
else
  # Check for vector DB fallback
  if curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
    echo "INDEX_MODE=vectordb"
  else
    echo "INDEX_MODE=none"
    echo "⚠ No indexer available. Run Phase 1 (distill) only — files will be indexed when graphify or Ollama becomes available."
  fi
fi
```

- If `graphify`: use `/graphify --update` in Phase 2 (graphify re-extracts new/changed files and merges into the existing graph)
- If `vectordb`: use `load-kb-to-memory.py` in Phase 2 (traditional embedding pipeline)
- If `none`: skip Phase 2. Files are written to `knowledgebase/` and will be found on the next successful index run.

## Architecture

```
Conversation ──► Phase 1 (Distill) ──► knowledgebase/*.yaml
                     │                        │
                     │  (Pi: skip memory)     ▼
                     │              Phase 2 (Index)
                     │             ┌───────────────────┐
                     │             │  graphify present? │
                     │             └───┬───────────┬───┘
                     │            yes  │           │ no
                     │                 ▼           ▼
                     │         /graphify        Ollama bge-large
                     │          --update       (1024-dim)
                     │              │               │
                     │              ▼               ▼
                     │     graphify-out/       agentdb.sqlite3
                     │     graph.json         ──searchable──► /search-kb
                     │     (self-contained)
                     │     ──searchable──►
                     │     /graphify query
                     │     /graphify explain
                     │     /graphify path
                     │
                     ▼
            (Claude only) memory/*.md
```

## Prerequisites

- `session-distillation` skill installed
- **For graphify mode:** `graphify` Python package (installed via `pip install graphifyy`) and an existing graph at `graphify-out/graph.json` (run `/graphify .` once to build)
- **For vector DB fallback:** Ollama running with `bge-large:latest` pulled — handled automatically by container entrypoint (`entrypoint-wrapper.sh`)
- Scripts at `/project/scripts/{load-kb-to-memory,search-kb-memory}.py` (only needed for vector DB fallback)
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

### Graphify mode (preferred)

If graphify is available, run an incremental update to merge new/changed files into the existing graph:

```bash
/graphify --update
```

This re-extracts only new/changed files since the last run and merges them into `graphify-out/graph.json`. Graphify stores relationships, community structure, and full document content — making the knowledgebase searchable via `/graphify query`, `/graphify explain`, and `/graphify path`.

**No external model or embedding server required.** The graph is self-contained.

Verify after indexing:

```bash
python3 -c "
import json
from pathlib import Path
if Path('graphify-out/graph.json').exists():
    data = json.loads(Path('graphify-out/graph.json').read_text())
    nodes = len(data.get('nodes', []))
    edges = len(data.get('links', data.get('edges', [])))
    print(f'graph.json: {nodes} nodes, {edges} edges')
else:
    print('graph.json not found — run /graphify . first')
"
```

### Vector DB fallback

If graphify is not available, build the vector index from knowledgebase YAML files:

```bash
python3 /project/scripts/load-kb-to-memory.py
```

This reads all `knowledgebase/{decisions,patterns,sessions}/*.yaml` files, generates 1024-dim embeddings via Ollama `bge-large:latest`, and stores them in `/project/.claude/agentdb.sqlite3`. Uses `INSERT OR REPLACE` — safe to run repeatedly.

**Do not proceed** if the embedding model check (Pre-flight) failed. Knowledgebase files are already written — they will be indexed on the next successful run.

Verify after indexing:

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.claude/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
"
```

## Phase 3 — Search

### Graphify mode (preferred)

Use graphify's query commands to find relevant prior knowledge:

```
/graphify query "embedding model decisions"
/graphify explain "bge-large Embedding Model"
/graphify path "Lean Container" "Embedding Pipeline"
```

Graphify provides structural answers: relationships between concepts, community membership, surprising connections, and shortest paths. These are capabilities that cosine similarity cannot provide.

### Vector DB fallback

Search the vector database:

```bash
python3 /project/scripts/search-kb-memory.py "<query>" [-n namespace] [-l limit]
```

Common namespaces: `decisions`, `patterns`, `sessions`.

### When to search

- **Before starting a new task** — has similar work been done before?
- **When hitting a problem** — search for relevant gotchas or troubleshooting patterns
- **When making a design decision** — find prior decisions and their rationale
- **Before proposing a solution** — check for rejected alternatives

## How Agents Use This

Agents treat the distill-and-index pipeline as a two-way memory system:

### Writing (Phase 1 → 2)

**On Pi (graphify mode):**
```
Agent completes work
  → distill-and-index runs (manual or PreCompact hook)
    → Phase 1: session-distillation scans conversation, writes KB files only
               (memory is skipped — pi-hermes-memory handles that independently)
    → Phase 2: /graphify --update merges new KB files into the knowledge graph
      → Knowledge becomes searchable via /graphify query, explain, path
```

**On Pi (vector DB fallback):**
```
Agent completes work
  → distill-and-index runs (manual or PreCompact hook)
    → Phase 1: session-distillation scans conversation, writes KB files only
    → Phase 2: KB files are embedded and indexed into SQLite vector DB
      → Knowledge becomes searchable by future sessions via /search-kb
```

**On Claude Code:** Same flow, but Phase 1 also writes memory files.

### Reading (Phase 3)

**Graphify mode:**
```
Agent starts new task
  → /graphify query "topic" — find related concepts and their connections
  → /graphify explain "node name" — understand what a concept is and what surrounds it
  → /graphify path "Concept A" "Concept B" — trace how two things are connected
  → Uses relationship context, community structure, and surprising connections
     to inform approach and avoid known traps
```

**Vector DB fallback:**
```
Agent starts new task
  → /search-kb "<topic>" to find relevant prior knowledge
    → "has anyone solved something like this before?"
    → "what decisions shaped this area of the code?"
    → "what gotchas should I watch out for?"
```

### Concrete agent patterns

**Before implementing a feature (graphify):**
1. `/graphify query "feature architecture"` — find related decisions and their connections
2. `/graphify explain "Component X"` — understand context around specific concepts
3. Apply known constraints, avoid rejected alternatives

**Before implementing a feature (vector DB):**
1. `/search-kb "<feature> architecture"` — find design decisions
2. `/search-kb "PATTERN#* <domain>"` — find relevant patterns
3. Apply known constraints, avoid rejected alternatives

**When debugging a problem (graphify):**
1. `/graphify query "error symptom"` — find related nodes and connections
2. `/graphify path "Error" "Root Cause"` — trace the dependency chain
3. Check community membership for related components

**When debugging a problem (vector DB):**
1. `/search-kb "<error message>"` — find past encounters
2. Check `knowledgebase/index.yaml` quickReference gotchas
3. Look for troubleshooting patterns matching the stack trace

**When a session ends (PreCompact hook):**
1. Distill findings into knowledgebase (skip memory on Pi)
2. Index: `/graphify --update` (preferred) or `load-kb-to-memory.py` (fallback)
3. Next session picks up from where this one left off

## Auto-Run via Hook

### On Pi — graphify mode

For automatic distillation before context compaction, add to `.pi/settings.local.json`:

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into knowledgebase files using session-distillation (skip memory — hermes-memory handles that). Phase 2: run /graphify --update to merge new files into the knowledge graph. Verify node counts.",
        "statusMessage": "Distilling session and updating knowledge graph..."
      }]
    }]
  }
}
```

### On Pi — vector DB fallback

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into knowledgebase files using session-distillation (skip memory — hermes-memory handles that). Phase 2: run python3 /project/scripts/load-kb-to-memory.py to index KB files into the vector database. Verify entry counts.",
        "statusMessage": "Distilling session and indexing into vector DB..."
      }]
    }]
  }
}
```

### On Claude Code — graphify mode

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into memory/KB files using session-distillation. Phase 2: run /graphify --update to merge new files into the knowledge graph. Verify node counts.",
        "statusMessage": "Distilling session and updating knowledge graph..."
      }]
    }]
  }
}
```

### On Claude Code — vector DB fallback

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Phase 1: distill conversation into memory/KB files using session-distillation. Phase 2: run python3 /project/scripts/load-kb-to-memory.py to index KB files into the vector database. Verify entry counts.",
        "statusMessage": "Distilling session and indexing into vector DB..."
      }]
    }]
  }
}
```

## Output

After running, confirm:

**Graphify mode (Pi):**
1. **KB entries created** — `cat knowledgebase/index.yaml`
2. **Graph updated** — `python3 -c "import json; d=json.loads(open('graphify-out/graph.json').read()); print(len(d.get('nodes',[])), 'nodes,', len(d.get('links',d.get('edges',[]))), 'edges')"`
3. **Search works** — `/graphify query "test query"`
4. **Memory untouched** — hermes-memory manages memory files independently

**Graphify mode (Claude Code):**
1. **Memory files written** — `ls ~/.claude/projects/*/memory/`
2. **MEMORY.md updated** — `cat ~/.claude/projects/*/memory/MEMORY.md`
3. **KB entries created** — `cat knowledgebase/index.yaml`
4. **Graph updated** — same as Pi
5. **Search works** — `/graphify query "test query"`

**Vector DB mode (Pi):**
1. **KB entries created** — `cat knowledgebase/index.yaml`
2. **Vector index populated** — `python3 -c "import sqlite3; db=sqlite3.connect('/project/.claude/agentdb.sqlite3'); print(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 'entries')"`
3. **Search works** — `/search-kb "test query" -l 3`
4. **Memory untouched** — hermes-memory manages memory files independently

**Vector DB mode (Claude Code):**
1. **Memory files written** — `ls ~/.claude/projects/*/memory/`
2. **MEMORY.md updated** — `cat ~/.claude/projects/*/memory/MEMORY.md`
3. **KB entries created** — `cat knowledgebase/index.yaml`
4. **Vector index populated** — `python3 -c "import sqlite3; db=sqlite3.connect('/project/.claude/agentdb.sqlite3'); print(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 'entries')"`
5. **Search works** — `/search-kb "test query" -l 3`