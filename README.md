# distill-rag-bridge

Persist coding agent conversation insights across sessions. Distills decisions, gotchas, architecture realities, and preferences into durable memory files that survive context compaction. Indexes them into a SQLite vector database for semantic search via `/search-kb`.

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
| `/distill-and-index` | Distill conversation into knowledgebase files, then index into vector DB |
| `/search-kb` | Semantic search over decisions, patterns, and sessions |
| `/setup-bridge` | One-time setup: verify prerequisites, configure PreCompact hook, build initial index |

## Dependencies (Container-Built)

The embedding model is pulled automatically by the container entrypoint at startup. No additional installation is required.

| Component | Model | Dims | Latency | Location |
|-----------|-------|------|---------|----------|
| **Embedding** | `bge-large:latest` (Ollama) | 1024 | ~330ms | `http://localhost:11434` (HTTP) |

The `bge-large:latest` model (~670MB) is pulled by `entrypoint-wrapper.sh` at container boot. Scripts at `/project/scripts/` use this model for all embedding operations.

> **Note:** If the bge-large model is not detected, indexing and search will abort. Ensure the container started correctly (`docker compose logs tooling | grep -E "ollama|bge-large|model"`).

## Usage

### Distill & Index
```
/distill-and-index
```
Runs the full pipeline: distill conversation → write knowledgebase files → index into vector database.

**On Pi:** Memory files are skipped — `pi-hermes-memory` handles memory independently. Only knowledgebase files are written and indexed.

**On Claude Code:** Both memory files and knowledgebase files are written and indexed.

### Semantic Search
```
/search-kb "<query>"
/search-kb "<query>" -n decisions -l 10
```
Searches decisions, patterns, and sessions by semantic similarity. Use before starting new work to find relevant prior knowledge.

### Automatic
The PreCompact hook runs distillation and indexing automatically before context compaction. Configure via `/setup-bridge`.

## Architecture

```
Conversation ──► Phase 1 (Distill) ──► knowledgebase/*.yaml
                     │                        │
                     │  (Pi: skip memory)     ▼
                     │              Phase 2 (Index)
                     │                        │
                     │           Ollama bge-large (1024-dim)
                     │           ── pulled by entrypoint ──
                     │                        │
                     │                        ▼
                     │          /project/.claude/agentdb.sqlite3
                     │          ──searchable via──► /search-kb
                     │
                     ▼
            (Claude only) memory/*.md
```

## Output

- **Knowledge base:** `<project>/knowledgebase/{decisions,patterns,sessions}/*.yaml` — structured, on-demand
- **Vector index:** `/project/.claude/agentdb.sqlite3` — searchable via `/search-kb`
- **Memory files (Claude Code only):** `~/.claude/projects/<project>/memory/*.md` — loaded every session via `MEMORY.md`

> **On Pi**, memory is managed by `pi-hermes-memory` — this plugin does not write memory files to avoid conflicts.

## License

MIT
