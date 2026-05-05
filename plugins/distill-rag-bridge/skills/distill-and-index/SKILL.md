---
name: distill-and-index
description: Distill conversation insights into durable memory/KB files, then index them into a vector database for semantic search. Use /search-kb to search prior knowledge.
allowed-tools: Bash Read Write Edit
---

# Distill & Index

Extract high-value information from a conversation and persist it so future sessions pick up where this one left off. Knowledgebase files are embedded and indexed into a vector database for semantic search.

## When to Use

- Ending a long or significant session
- After completing a milestone or phase
- Before context compaction
- The user explicitly asks to save insights for future sessions

## Architecture

```
Conversation ──► Phase 1 (Distill) ──► memory/*.md + knowledgebase/*.yaml
                                              │
                                     Phase 2 (Index)
                                              │
                                 embed-server (primary, ~40ms)
                                 Ollama HTTP  (fallback, ~330ms)
                                              │
                                              ▼
                                    /project/.claude/agentdb.sqlite3
                                    ──searchable via──► /search-kb
```

Embedding uses `embed-server.py` daemon (sentence-transformers, 40ms) with Ollama HTTP fallback (330ms) — no npm dependencies.

## Prerequisites

- `session-distillation` skill installed
- `embed-server.py` daemon running (auto-started at container boot, Unix socket `/tmp/embed-server.sock`)
- Ollama running with `all-minilm:latest` pulled (fallback, auto-pulled at container start)
- Scripts at `/project/scripts/{embed-server,load-kb-to-memory,search-kb-memory}.py`

## Pre-flight: Embedding Model Check

Before any indexing or search operation, verify the embedding model is available:

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
  echo "✖ No embedding model available — aborting."
  echo "Check: docker compose logs tooling | grep -E 'embed|ollama|model'"
  exit 1
fi
```

**Do not proceed** with Phase 2 (Index) or Phase 3 (Search) if this check fails. Phase 1 (Distill) can still run — memory/KB files will be written and indexed on the next successful start.

## Phase 1 — Distill (always runs)

Run the session-distillation workflow:

1. **Scan** the conversation for decisions, gotchas, architecture realities, user preferences, bug root causes, integration details, troubleshooting procedures, and operational risks
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
4. **Write knowledge base entries** — YAML files for decisions, patterns, and sessions
5. **Update index files** — `MEMORY.md` and `index.yaml`
6. **Verify** — no duplicates, no stale entries, index counts accurate

## Phase 2 — Index (opt-in)

Build the vector index from knowledgebase YAML files:

```bash
python3 /project/scripts/load-kb-to-memory.py
```

This reads all `knowledgebase/{decisions,patterns,sessions}/*.yaml` files, generates 384-dim embeddings (fast via embed daemon, fallback to Ollama), and stores them in `/project/.claude/agentdb.sqlite3`. Uses `INSERT OR REPLACE` — safe to run repeatedly.

Verify after indexing:

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.claude/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
"
```

## Phase 3 — Semantic Search

Search the vector database for relevant prior knowledge:

```bash
python3 /project/scripts/search-kb-memory.py "<query>" [-n namespace] [-l limit]
```

Common namespaces: `decisions`, `patterns`, `sessions`.

### When to search

- **Before starting a new task** — has similar work been done before?
- **When hitting a problem** — search for relevant gotchas or troubleshooting patterns
- **When making a design decision** — find prior decisions and their rationale
- **Before proposing a solution** — check for rejected alternatives

Users can also invoke search directly via `/search-kb <query>`.

## How Agents Use This

Agents treat the distill-and-index pipeline as a two-way memory system:

### Writing (Phase 1 → 2)

```
Agent completes work
  → distill-and-index runs (manual or PreCompact hook)
    → Phase 1: session-distillation scans conversation, writes memory + KB files
    → Phase 2: files are embedded and indexed into SQLite vector DB
      → Knowledge becomes searchable by future sessions
```

### Reading (Phase 3)

```
Agent starts new task
  → runs /search-kb "<topic>" to find relevant prior knowledge
    → "has anyone solved something like this before?"
    → "what decisions shaped this area of the code?"
    → "what gotchas should I watch out for?"
  → uses findings to inform approach, skip solved problems, avoid known traps
```

### Concrete agent patterns

**Before implementing a feature:**
1. `/search-kb "<feature> architecture"` — find design decisions
2. `/search-kb "PATTERN#* <domain>"` — find relevant patterns
3. Apply known constraints, avoid rejected alternatives

**When debugging a problem:**
1. `/search-kb "<error message>"` — find past encounters
2. Check `knowledgebase/index.yaml` quickReference gotchas
3. Look for troubleshooting patterns matching the stack trace

**When a session ends (PreCompact hook):**
1. Distill findings automatically
2. Index into vector DB (`load-kb-to-memory.py`)
3. Next session picks up from where this one left off

## Auto-Run via Hook

For automatic distillation before context compaction, add to `.claude/settings.local.json`:

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

The `load-kb-to-memory.py` script is safe to run repeatedly — `INSERT OR REPLACE` ensures idempotency.

## Output

After running, confirm:

1. **Memory files written** — `ls ~/.claude/projects/*/memory/`
2. **MEMORY.md updated** — `cat ~/.claude/projects/*/memory/MEMORY.md`
3. **KB entries created** — `cat knowledgebase/index.yaml`
4. **Vector index populated** — `python3 -c "import sqlite3; db=sqlite3.connect('/project/.claude/agentdb.sqlite3'); print(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 'entries')"`
5. **Search works** — `/search-kb "test query" -l 3`
