---
name: setup-bridge
description: Check prerequisites, configure PreCompact hook, and verify the knowledgebase vector search pipeline
---

# Setup Bridge

Configure the distill-rag-bridge plugin. Run this once after installation.

## Quick Start

```bash
claude plugin marketplace add /home/tool/.local/share/skill-marketplace 2>/dev/null || true
claude plugin install distill-rag-bridge@distill-rag-bridge 2>/dev/null || true
# /reload-plugins
# /setup-bridge
```

## What This Command Does

1. **Checks prerequisites** — verifies session-distillation is available, Ollama is running, model is pulled
2. **Configures PreCompact hook** — writes `.claude/settings.local.json` for auto-distillation before context compaction
3. **Runs initial index** — builds the vector database from existing knowledgebase files

## Prerequisites

- `session-distillation` skill installed
- Ollama installed and running
- `all-minilm:latest` model pulled (45 MB) — pulled automatically at container/VM start

## Steps

### 0. Pre-flight: Verify Embedding Model

The bridge requires an embedding model. Check that at least one is available before proceeding:

```bash
# Check primary: embed-server daemon
if test -S /tmp/embed-server.sock; then
  echo "✔ embed-server daemon running (all-MiniLM-L6-v2, ~40ms)"
  EMBED_READY=true
# Check fallback: Ollama model
elif curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('all-minilm' in m for m in models) else 1)" 2>/dev/null; then
  echo "✔ Ollama fallback available (all-minilm:latest, ~330ms)"
  EMBED_READY=true
else
  echo "✖ No embedding model detected!"
  echo ""
  echo "  Neither the embed-server daemon (/tmp/embed-server.sock)"
  echo "  nor the Ollama fallback model (all-minilm:latest) is available."
  echo ""
  echo "  This is a container-built dependency. Check container startup:"
  echo "    docker compose logs tooling | grep -E 'embed|ollama|model'"
  echo ""
  echo "  Aborting setup. Fix the container and retry."
  exit 1
fi
```

**Do not proceed** if this check fails. The bridge cannot index or search knowledge without an embedding model.

### 1. Verify session-distillation

```bash
ls .claude/skills/session-distillation  # or check /reload-plugins lists it
```

### 2. Configure auto-distillation

Run this command, or manually create `.claude/settings.local.json`:

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
claude plugin list | grep distill-rag-bridge
# Should show: ✔ enabled

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
