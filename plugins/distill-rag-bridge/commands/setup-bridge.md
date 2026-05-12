---
name: setup-bridge
description: Check prerequisites, configure PreCompact hook, and verify the knowledgebase vector search pipeline
---

# Setup Bridge

Configure the distill-rag-bridge plugin. Run this once after installation.

## Quick Start

```
/setup-bridge
```

## What This Command Does

1. **Checks prerequisites** — verifies session-distillation is available, Ollama is running, bge-large model is pulled (by entrypoint)
2. **Configures PreCompact hook** — writes `.pi/settings.local.json` (Pi) or `.claude/settings.local.json` (Claude) for auto-distillation before context compaction
3. **Runs initial index** — builds the vector database from existing knowledgebase files

## Prerequisites

- `session-distillation` skill installed
- Ollama installed and running
- `bge-large:latest` model pulled (~670 MB) — pulled automatically by container entrypoint (`entrypoint-wrapper.sh`)

## Steps

### 0. Pre-flight: Verify Embedding Model

The bridge requires an embedding model. The container entrypoint pulls `bge-large:latest` at startup. Verify it's available before proceeding:

```bash
if curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
  echo "✔ bge-large ready (pulled by entrypoint)"
else
  echo "✖ bge-large not detected!"
  echo ""
  echo "  The bge-large:latest model is not available."
  echo ""
  echo "  This is pulled automatically by the container entrypoint."
  echo "  Check container startup:"
  echo "    docker compose logs tooling | grep -E 'ollama|bge-large|model'"
  echo ""
  echo "  Aborting setup. Fix the container and retry."
  exit 1
fi
```

**Do not proceed** if this check fails.

### 1. Verify session-distillation

```bash
ls .claude/skills/session-distillation  # Claude Code
# or check available skills list for Pi
```

### 2. Configure auto-distillation

**On Pi** — create `.pi/settings.local.json`:

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

**On Claude Code** — create `.claude/settings.local.json`:

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

### 3. Verify vector search pipeline

```bash
# Check Ollama is running and model is available
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print('Models:', [m['name'] for m in d.get('models',[])])"

# Verify scripts exist
ls -l /project/scripts/load-kb-to-memory.py
ls -l /project/scripts/search-kb-memory.py

# Build the vector index from existing KB files
python3 /project/scripts/load-kb-to-memory.py

# Test semantic search
python3 /project/scripts/search-kb-memory.py "HTLC atomic swap settlement" -l 3
```

### 4. Verify

```bash
# Confirm vector DB is populated
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.claude/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
"

# Run a search through the skill
# /search-kb "architecture decisions"
```

Run `/distill-and-index` to test the full pipeline.
