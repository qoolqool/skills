---
name: setup-bridge
description: Check prerequisites, configure PreCompact hook, and verify the knowledgebase search pipeline. Detects vector DB and Central KB availability.
---

# Setup Bridge

Configure the distill-rag-bridge plugin. Run this once after installation.

## Quick Start

```
/setup-bridge
```

## What This Command Does

1. **Detects available indexers** — checks for embed-server or Ollama (vector DB) and Central KB server
2. **Verifies prerequisites** — session-distillation available, necessary models/scripts present
3. **Configures PreCompact hook** — writes `.pi/settings.local.json` (Pi) or `.claude/settings.local.json` (Claude)
4. **Runs initial index** — builds the search index from existing knowledgebase files

## Step 0 — Detect Index Mode

Determine which indexing systems are available:

```bash
INDEX_MODES=()

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
# - kb search/pull: only needs server healthy (server generates query vectors)
# - kb submit: also needs client-side embedding source
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
fi
```

## Step 1 — Verify Prerequisites

### 1a — Verify session-distillation (always required)

```bash
ls .claude/skills/session-distillation  # Claude Code
# or check available skills list for Pi
```

### 1b — Verify embedding source (if vectordb or central-kb detected)

At least one embedding source must be available:

```bash
# Check embed-server socket
[ -S /tmp/embed-server.sock ] && echo "✔ embed-server socket ready" || true

# Check embed-server HTTP
curl -sf http://host.containers.internal:9001/health 2>/dev/null && echo "✔ embed-server HTTP ready" || true

# Check Ollama bge-large
curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" && echo "✔ Ollama bge-large ready"
```

On a fresh clone with no model pulled, the embed-server HTTP sidecar at `host.containers.internal:9001` provides embeddings automatically.

## Step 2 — Configure Auto-Distillation

### On Pi

Create `.pi/settings.local.json`:

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

Create `.claude/settings.local.json`:

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

## Step 3 — Build Initial Index

### 3a — Vector DB (local)

Build the vector index from existing KB files:

```bash
# Verify scripts exist
ls -l /project/scripts/load-kb-to-memory.py
ls -l /project/scripts/search-kb-memory.py

# Build the vector index
python3 /project/scripts/load-kb-to-memory.py
```

Verify:

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.claude/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
print(f'  Total: {db.execute(\"SELECT COUNT(*) FROM embeddings\").fetchone()[0]}')
"
```

### 3b — Central KB (shared)

Submit entries to Central KB:

```bash
# Verify kb CLI
kb health

# Submit entries
kb submit --project $CENTRAL_KB_PROJECT

# Verify
kb search "test" --scope $CENTRAL_KB_PROJECT
```

## Step 4 — Verify Search

Test both search backends:

```bash
# Local vector DB
python3 /project/scripts/search-kb-memory.py "architecture decisions" -l 3

# Central KB (if available)
kb search "architecture" --scope $CENTRAL_KB_PROJECT
```

Run `/distill-and-index` to test the full pipeline. It will auto-detect the available indexers and use all that are present.