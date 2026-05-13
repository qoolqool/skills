# distill-rag-bridge

Persist coding agent conversation insights across sessions. Distills decisions, gotchas, architecture realities, and preferences into durable knowledgebase files that survive context compaction. Indexes them for search using graphify (preferred) or vector DB fallback.

## Dual-Mode Architecture

The plugin auto-detects the best available indexing method:

| Mode | Detection | Index | Search | Size |
|------|-----------|-------|--------|------|
| **Graphify** ✅ preferred | `graphify` package + `graphify-out/graph.json` | `/graphify --update` (structural graph) | `/graphify query`, `/graphify explain`, `/graphify path` | Self-contained JSON (~KB) |
| **Vector DB** fallback | Ollama + `bge-large:latest` | `load-kb-to-memory.py` (embeddings) | `/search-kb` (cosine similarity) | ~670 MB model + SQLite |

**Why graphify is preferred:** It stores relationships, communities, and full document content in one self-contained file. No external model, no embedding server, no separate database. Graph traversal answers questions that cosine similarity cannot — like "how does X connect to Y?" or "what are the cross-cutting patterns?"

## Installation

Add the marketplace as a submodule, or clone directly:

```bash
git clone https://github.com/qoolqool/skills.git ~/.local/share/skill-marketplace
```

Register the plugin in `~/.pi/agent/settings.json` under `"packages"`:

```json
"../../.local/share/skill-marketplace/plugins/distill-rag-bridge"
```

Run setup once to configure auto-distillation:

```
/setup-bridge
```

**Prerequisite:** `session-distillation` skill.

## Skills

| Skill | Purpose |
|-------|---------|
| `/distill-and-index` | Distill conversation into knowledgebase files, then index (graphify or vector DB) |
| `/search-kb` | Search knowledgebase — uses graphify (preferred) or vector DB fallback |
| `/setup-bridge` | One-time setup: detect indexer, verify prerequisites, configure PreCompact hook, build initial index |

## Dependencies

### Graphify mode (preferred)

| Component | Install | Notes |
|-----------|---------|-------|
| **graphify** | `pip install graphifyy` | Self-contained — no external model or DB needed |
| **Initial graph** | `/graphify .` (run once) | Builds `graphify-out/graph.json` from project files |

No Ollama, no embedding model, no SQLite database. The graph is a single JSON file.

### Vector DB fallback

| Component | Model | Dims | Latency | Location |
|-----------|-------|------|---------|----------|
| **Embedding** | `bge-large:latest` (Ollama) | 1024 | ~330ms | `http://localhost:11434` (HTTP) |

The `bge-large:latest` model (~670MB) is pulled by `entrypoint-wrapper.sh` at container boot. Scripts at `/project/scripts/` use this model for all embedding operations.

> **Note:** If the bge-large model is not detected, indexing and search will abort. Install graphify instead, or ensure the container started correctly (`docker compose logs tooling | grep -E "ollama|bge-large|model"`).

## Usage

### Distill & Index
```
/distill-and-index
```
Runs the full pipeline: distill conversation → write knowledgebase files → index.

**Auto-detects:** If graphify is available, runs `/graphify --update`. Otherwise, indexes into the vector DB via `load-kb-to-memory.py`.

**On Pi:** Memory files are skipped — `pi-hermes-memory` handles memory independently. Only knowledgebase files are written and indexed.

**On Claude Code:** Both memory files and knowledgebase files are written and indexed.

### Search Knowledgebase

**Graphify mode:**
```
/graphify query "embedding model decisions"           # broad context (BFS)
/graphify explain "bge-large Embedding Model"          # everything about one concept
/graphify path "Lean Container" "Embedding Pipeline"  # how two things connect
```

**Vector DB fallback:**
```
/search-kb "<query>"
/search-kb "<query>" -n decisions -l 10
```

### Automatic
The PreCompact hook runs distillation and indexing automatically before context compaction. Configure via `/setup-bridge`.

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

## Output

- **Knowledge base:** `<project>/knowledgebase/{decisions,patterns,sessions}/*.yaml` — structured, on-demand
- **Graphify mode:** `graphify-out/graph.json` — self-contained knowledge graph with relationships, communities, and full content. Searchable via `/graphify query|explain|path`.
- **Vector DB mode:** `/project/.claude/agentdb.sqlite3` — searchable via `/search-kb`
- **Memory files (Claude Code only):** `~/.claude/projects/<project>/memory/*.md` — loaded every session via `MEMORY.md`

> **On Pi**, memory is managed by `pi-hermes-memory` — this plugin does not write memory files to avoid conflicts.

## License

MIT