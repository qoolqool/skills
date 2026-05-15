---
name: setup-bridge
description: Check prerequisites, configure PreCompact hook, and verify the knowledgebase search pipeline. Detects graphify (preferred) or vector DB fallback.
---

# Setup Bridge

Configure the distill-rag-bridge plugin. Run this once after installation.

## Quick Start

```
/setup-bridge
```

## What This Command Does

1. **Detects available indexer** — checks for graphify first (preferred), then vector DB fallback
2. **Verifies prerequisites** — session-distillation available, necessary models/scripts present
3. **Configures PreCompact hook** — writes `.pi/settings.local.json` (Pi) or `.claude/settings.local.json` (Claude)
4. **Runs initial index** — builds the search index from existing knowledgebase files

## Step 0 — Detect Index Mode

Determine which search system to use:

```bash
# Check for vector DB (preferred)
if curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
  echo "MODE=vectordb (Ollama bge-large available)"
else
  # Check for graphify fallback
  if python3 -c "import graphify" 2>/dev/null; then
    if [ -f graphify-out/graph.json ]; then
      echo "MODE=graphify (existing graph found)"
    else
      echo "MODE=graphify-init (graphify installed, no graph yet — will build)"
    fi
  else
    echo "ERROR: Neither Ollama bge-large nor graphify is available."
    echo "  Ensure Ollama is running with bge-large:latest model."
    echo "  Or install graphify: pip install graphifyy"
    exit 1
  fi
fi
```

**If vector DB is available**, proceed with the Ollama/bge-large prerequisites.

**If only graphify is available**, skip Steps 1.1 and 3.1 — no embedding model or vector DB scripts needed.

## Step 1 — Verify Prerequisites

### 1a — Verify session-distillation (always required)

```bash
ls .claude/skills/session-distillation  # Claude Code
# or check available skills list for Pi
```

### 1b — Verify embedding model (vector DB mode)

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

**Do not proceed** with vector DB mode if this check fails.

Skip this step if graphify fallback mode was detected.

## Step 2 — Configure Auto-Distillation

### Vector DB mode (Pi)

Create `.pi/settings.local.json`:

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

### Vector DB mode (Claude Code)

Create `.claude/settings.local.json`:

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

### Graphify fallback mode (Pi)

Create `.pi/settings.local.json`:

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

### Graphify fallback mode (Claude Code)

Create `.claude/settings.local.json`:

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

## Step 3 — Build Initial Index

### 3a — Vector DB mode

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
"
```

Test search:

```
/search-kb "architecture decisions"
```

### 3b — Graphify fallback mode

If a graph already exists, run an incremental update:

```bash
/graphify --update
```

If no graph exists yet, build one from the project:

```bash
/graphify .
```

Verify:

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
    print('ERROR: graph.json not found')
"
```

Test search:

```
/graphify query "architecture decisions"
```

## Step 4 — Verify

Run `/distill-and-index` to test the full pipeline. It will auto-detect the available indexer and use the appropriate mode.