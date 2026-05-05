# distill-rag-bridge

Persist Claude Code conversation insights across sessions. Distills decisions, gotchas, architecture realities, and preferences into durable memory files that survive context compaction. Optionally indexes them into AgentDB for semantic vector search.

## Installation

```bash
# Add the marketplace
claude plugin marketplace add https://github.com/qoolqool/skills

# Install the plugin
claude plugin install distill-rag-bridge@distill-rag-bridge

# Reload plugins
# /reload-plugins

# Run setup (configures auto-distillation)
# /setup-bridge
```

**Prerequisite:** `session-distillation` skill (from official Claude Code plugin marketplace or `.agents/` directory).

## Dependencies (Container-Built)

The embedding infrastructure is built into the container image and available at container start. No additional installation is required.

| Component | Model | Dims | Latency | Location |
|-----------|-------|------|---------|----------|
| **Primary** | `all-MiniLM-L6-v2` (sentence-transformers) | 384 | ~40ms | `/tmp/embed-server.sock` (Unix socket) |
| **Fallback** | `all-minilm:latest` (Ollama) | 384 | ~330ms | `http://localhost:11434` (HTTP) |

Both models produce 384-dimensional embeddings. The embed-server daemon starts automatically at container boot. If the daemon is unavailable, scripts fall back to Ollama automatically.

> **Note:** If neither the embed daemon socket nor the Ollama model is detected, the setup and search commands will abort. Ensure the container started correctly (`docker compose logs tooling | grep -E "embed|ollama"`).

## Usage

### Manual
```
/distill-and-index
```

### Automatic
The PreCompact hook runs distillation automatically before context compaction. No manual action needed.

## Optional: Vector Search

For semantic search across distilled memories, additionally install:

```bash
claude plugin install ruflo-rag-memory@ruflo
```

Then run `/distill-and-index` — indexing happens automatically after distillation if rag-memory is present.

## Output

- **Memory files:** `~/.claude/projects/<project>/memory/*.md` — loaded every session via `MEMORY.md`
- **Knowledge base:** `<project>/knowledgebase/{decisions,patterns,sessions}/*.yaml` — structured, on-demand

## License

MIT
