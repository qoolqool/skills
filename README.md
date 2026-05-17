# distill-rag-bridge

Persist coding agent conversation insights across sessions. Distills decisions, gotchas, architecture realities, and preferences into durable knowledgebase files that survive context compaction. Indexes them for search using Vector DB (local) and Central KB (shared).

## Two-Tier Indexing

| Mode | Scope | Index Method | Search Method | Dependencies |
|------|-------|-------------|---------------|-------------|
| **Vector DB** | Local | `load-kb-to-memory.py` (cosine similarity over 1024-dim embeddings) | `search-kb-memory.py` or `search-kb` skill | embed-server or Ollama + `bge-large:latest` |
| **Central KB** | Shared (cross-project) | `kb submit` (pushes entries to shared server) | `kb search`, `kb explain` | `kb` CLI + Central KB server running |

Both tiers can run simultaneously — Vector DB for fast local search, Central KB for cross-project knowledge sharing.

## Skills

| Skill | Description |
|-------|-------------|
| `/distill-and-index` | Distill conversation into knowledgebase files, then index (Vector DB + Central KB) |
| `/search-kb` | Search knowledgebase across all available backends (local Vector DB + Central KB) |

## Prerequisites

### For Vector DB mode

| Requirement | How | Check |
|-------------|-----|-------|
| embed-server or Ollama | embed-server socket/HTTP (~40-100ms) or Ollama (~330ms) | see pre-flight |
| bge-large model | Auto-detected; pulled by `entrypoint-wrapper.sh` if Ollama is source | `ollama list \| grep bge-large` |
| Python 3.11+ | Pre-installed | `python3 --version` |
| Scripts | `/project/scripts/{load-kb-to-memory,search-kb-memory}.py` | `ls /project/scripts/` |

### For Central KB mode

| Requirement | How | Check |
|-------------|-----|-------|
| `kb` CLI | Installed by Dockerfile or install script | `command -v kb` |
| Central KB server running | Docker container `tooling-central` | `kb health` |
| Embedding source for submit | Same as Vector DB (embed-server or Ollama) | see pre-flight |

> **No model pull needed on fresh clone.** The embed-server HTTP sidecar at `host.containers.internal:9001` provides embeddings without downloading any model.

## Quick Start

### Indexing (distill-and-index)

Auto-detects available indexers:

```bash
# Run the skill — it detects what's available and uses all of it
/distill-and-index
```

If Vector DB + Central KB are both available, both run. If neither, only Phase 1 (distill) runs.

### Search (search-kb)

```bash
# Local search
python3 /project/scripts/search-kb-memory.py "embedding decisions" -n decisions

# Central KB search (no local model needed — server generates query vectors)
kb search "embedding decisions" --scope my-project

# Structured explain — agent synthesizes narrative
kb explain "embedding decisions" --scope my-project
```

The `search-kb` skill searches all available backends and the agent synthesizes a unified narrative.

## Architecture

```
Conversation ──► distill-and-index ──► knowledgebase/*.yaml
                     │                        │
                     │  (Pi: skip memory)       ▼
                     │              detect available indexers
                     │             ┌──────────────────────┐
                     │             │  embed-server socket?  │
                     │             │  embed-server HTTP?    │
                     │             │  Ollama bge-large?     │
                     │             └────┬──────────┬────────┘
                     │             vectordb    central-kb
                     │                 │            │
                     │                 ▼            ▼
                     │          load-kb-to-    kb submit
                     │          memory.py     (1024-dim)
                     │              │            │
                     │              ▼            ▼
                     │        agentdb.      Central KB
                     │        sqlite3       server
                     │        (local)    (shared,
                     │                   cross-project)
                     │          │            │
                     │          ▼            ▼
                     │    search-kb     kb search/explain
                     │    memory.py     kb pull/drift
                     │
                     ▼
            (Claude only) memory/*.md
```

## Output Files

| File | Mode | Searchable via |
|------|------|---------------|
| `agentdb.sqlite3` | Vector DB (local) | `search-kb-memory.py` or `search-kb` skill |
| Central KB server | Central KB (shared) | `kb search`, `kb explain`, `search-kb` skill |