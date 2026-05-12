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
| `/distill-and-index` | Distill conversation into memory + KB files, then index into vector DB |
| `/search-kb` | Semantic search over decisions, patterns, and sessions |
| `/setup-bridge` | One-time setup: verify prerequisites, configure PreCompact hook, build initial index |

## Dependencies (Container-Built)

The embedding infrastructure is built into the container image and available at container start. No additional installation is required.

| Component | Model | Dims | Latency | Location |
|-----------|-------|------|---------|----------|
| **Primary** | `BAAI/bge-small-en-v1.5` (sentence-transformers) | 384 | ~40ms | `/tmp/embed-server.sock` (Unix socket) |
| **Fallback** | `bge-small:latest` (Ollama) | 384 | ~330ms | `http://localhost:11434` (HTTP) |

Both models produce 384-dimensional embeddings. The embed-server daemon starts automatically at container boot. If the daemon is unavailable, scripts fall back to Ollama automatically.

> **Note:** If neither the embed daemon socket nor the Ollama model is detected, the setup and search commands will abort. Ensure the container started correctly (`docker compose logs tooling | grep -E "embed|ollama"`).

## Usage

### Distill & Index
```
/distill-and-index
```
Runs the full pipeline: distill conversation → write memory/KB files → index into vector database.

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

## Output

- **Memory files:** `~/.claude/projects/<project>/memory/*.md` — loaded every session via `MEMORY.md`
- **Knowledge base:** `<project>/knowledgebase/{decisions,patterns,sessions}/*.yaml` — structured, on-demand
- **Vector index:** `/project/.claude/agentdb.sqlite3` — searchable via `/search-kb`

## License

MIT
