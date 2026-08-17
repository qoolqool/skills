---
name: distill-and-index
description: Distill conversation insights into durable knowledgebase files (OKF v0.2). Indexing (vector DB / Central KB) is DISABLED by default pending redesign — opt in via DISTILL_INDEX_ENABLED=1.
allowed-tools: Bash Read Write Edit
---

# Distill & Index

Extract high-value information from a conversation and persist it so future sessions pick up where this one left off. Knowledgebase files use the **Open Knowledge Format (OKF) v0.2** — markdown files with YAML frontmatter. Legacy YAML entries and OKF v0.1 entries are auto-detected and migrated.

> **⚠ Indexing is DISABLED by default.** Phase 2 (Index) is being redesigned and does NOT run unless you explicitly opt in by setting `DISTILL_INDEX_ENABLED=1`. By default only **Phase 1 (Distill)** runs — it writes OKF v0.2 knowledgebase files. The sections below describing vector DB / Central KB indexing are retained for reference during the redesign; they are gated behind the flag and skipped unless enabled.

## Two-Tier Indexing (DISABLED by default — redesign pending)

This skill detects which indexing systems are available and uses **all that are present**:

| Mode | Scope | Detection | Index Method | Search Method |
|------|-------|-----------|-------------|----------------|
| **Vector DB** | Local | embed-server or Ollama available + `bge-small` model | `load-kb-to-memory.py` (cosine similarity over embeddings) | `search-kb` skill |
| **Central KB** | Shared | `kb` CLI on PATH + `kb health` succeeds | `kb submit` (384-dim embeddings, auto-detected server URL) | `search-kb` skill |

**Local vs Shared:** Vector DB is a **local** index — knowledge stays in this project. Central KB is a **shared** index — knowledge is pushed to a server where other projects and sessions can discover it. Both can run in parallel.

**Why Central KB matters:** It enables cross-project knowledge sharing. Decisions, patterns, and troubleshooting procedures from one project become searchable by any project on the same Central KB server.

## Embedding Strategy

All indexing requires 384-dim embeddings. The embedding source is detected in priority order:

| Priority | Source | Speed | How |
|-----------|--------|-------|-----|
| 1 | **embed-server** (Central KB sidecar, HTTP) | ~100ms | HTTP at `host.containers.internal:9001`, `POST /embed {"text":"..."}` |
| 2 | **Ollama** (fallback) | ~330ms | HTTP at `localhost:11434/api/embeddings`, model `bge-small:latest` |

**In this project:** Docker Compose via `entrypoint-wrapper.sh` starts `embed-server.py` automatically, which loads the embedding model (`BAAI/bge-small-en-v1.5`, 384-dim) via Hugging Face `sentence-transformers`. **No Ollama model download needed** — embeddings are served via HTTP at `host.containers.internal:9001`.

- `load-kb-to-memory.py` and `search-kb-memory.py` use `kb_common.py` which tries embed-server HTTP (port 9001) → Ollama fallback
- `kb submit` uses client-side embeddings from the same pipeline
- **Never mix embedding dimensions** — all entries must be 384-dim

## Platform Behavior

| Platform | Memory files | Knowledgebase files | Index |
|----------|-------------|---------------------|-------|
| **Pi** | ❌ Skipped — handled by `pi-hermes-memory` | ✅ decisions, patterns, sessions (OKF `.md`) | ✅ vector DB, Central KB |
| **Claude Code** | ✅ `~/.claude/projects/*/memory/` | ✅ decisions, patterns, sessions (OKF `.md`) | ✅ vector DB, Central KB |

**On Pi**, do NOT write memory entries (`MEMORY.md`, `USER.md`, etc.). The `pi-hermes-memory` extension already manages all memory — writing duplicate entries causes conflicts. Focus exclusively on knowledgebase distillation and indexing.

**On Claude Code**, write both memory and knowledgebase files as described in Phase 1.

## When to Use

- Ending a long or significant session
- After completing a milestone or phase
- Before context compaction
- The user explicitly asks to save insights for future sessions

## Pre-flight: Detect & Convert Format

Before Phase 1, detect whether the existing knowledgebase uses legacy YAML or OKF v0.1 format and migrate to OKF v0.2:

```bash
KB_DIR="/project/knowledgebase"
HAS_LEGACY=false
HAS_V01=false

# Check for legacy YAML files
if ls "$KB_DIR"/decisions/*.yaml "$KB_DIR"/patterns/*.yaml "$KB_DIR"/sessions/*.yaml 2>/dev/null; then
  HAS_LEGACY=true
  echo "⚠ Legacy YAML files detected. Converting to OKF..."
  python3 /project/scripts/migrate-to-okf.py \
    --input-dir "$KB_DIR" \
    --output-dir "$KB_DIR"
  echo "✅ Conversion complete. Legacy YAML files remain in place; OKF .md files created alongside."
fi

# Check for OKF v0.1 files (using timestamp instead of generated, or no sources/verified/status)
if grep -rl '^timestamp:' "$KB_DIR"/decisions/*.md "$KB_DIR"/patterns/*.md "$KB_DIR"/sessions/*.md 2>/dev/null; then
  HAS_V01=true
  echo "⚠ OKF v0.1 files detected (using legacy 'timestamp' field). Migrating to v0.2..."
  python3 /project/scripts/migrate-okf-v01-to-v02.py \
    --input-dir "$KB_DIR" \
    --output-dir "$KB_DIR" 2>/dev/null || \
  echo "  ⚠ Migration script not found. Manual migration needed: replace 'timestamp:' with 'generated: { by: <actor>, at: <timestamp> }' and add 'status: stable'."
fi

# Verify OKF v0.2 format
if [ "$HAS_LEGACY" = true ] || [ "$HAS_V01" = true ] || ls "$KB_DIR"/decisions/*.md "$KB_DIR"/patterns/*.md "$KB_DIR"/sessions/*.md 2>/dev/null; then
  python3 -c "
import sys
sys.path.insert(0, '/project/tooling/central-kb')
from app.okf import validate_okf_bundle
errors = validate_okf_bundle('$KB_DIR')
if errors:
    for e in errors:
        print(f'  ✗ {e}')
    sys.exit(1)
else:
    print('✅ Knowledgebase is OKF v0.2 conformant')
" 2>/dev/null || echo "⚠ OKF validation unavailable (app.okf module not importable)"
fi
```

Then, **only if `DISTILL_INDEX_ENABLED=1`**, detect which indexing modes are available:

```bash
INDEX_MODES=[]
if [ "${DISTILL_INDEX_ENABLED:-0}" != "1" ]; then
  echo "INDEX_MODE=none (DISTILL_INDEX_ENABLED != 1 — indexing disabled by default)"
  echo "⚠ Phase 2 skipped. Run Phase 1 (distill) only."
  exit 0
fi

# Check for vector DB — embed-server (HTTP) or Ollama
HAS_EMBED=false
if curl -sf http://host.containers.internal:9001/health 2>/dev/null | python3 -c "
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
sys.exit(0 if any('bge-small' in m for m in models) else 1)" 2>/dev/null; then
  HAS_EMBED=true
  INDEX_MODES+=("vectordb")
  echo "Embedding: Ollama (~330ms)"
fi

# Check for Central KB (shared index)
if command -v kb &>/dev/null && kb health &>/dev/null; then
  if [ "$HAS_EMBED" = true ]; then
    INDEX_MODES+=("central-kb")
  else
    echo "⚠ Central KB: server reachable for search/pull, but no client-side embedding source — submit skipped"
  fi
fi

if [ ${#INDEX_MODES[@]} -eq 0 ]; then
  echo "INDEX_MODE=none"
  echo "⚠ No indexer available. Run Phase 1 (distill) only."
else
  echo "INDEX_MODES=${INDEX_MODES[*]}"
fi
```

- If `vectordb`: run `load-kb-to-memory.py` — embeds entries and stores in local SQLite
- If `central-kb`: run `kb submit` — pushes entries to shared Central KB server
- Both can be active simultaneously
- If `none`: skip Phase 2. Files are written to `knowledgebase/` and will be indexed on the next successful run.

## Architecture

```
Conversation ──► Pre-flight ──► knowledgebase/*.yaml (legacy)
                     │               │
                     │          auto-convert
                     │               ▼
                     │     knowledgebase/*.md (OKF v0.1)
                     │               │
                     │          migrate v0.1→v0.2
                     │               ▼
                     │     knowledgebase/*.md (OKF v0.2)
                     │     with generated, verified,
                     │     status, sources, stale_after
                     │               │
                     │  (Pi: skip memory)     ▼
                     │              Phase 2 (Index) — all available run in parallel
                     │             ┌─────────────────────┐
                     │             │ detect available     │
                     │             │ indexers & embedders│
                     │             └───┬──────────┬───────┘
                     │            vectordb   central-kb
                     │                │           │
                     │                ▼           ▼
                     │        load-kb-to-    kb submit
                     │        memory.py     (384-dim)
                     │             │           │
                     │             ▼           ▼
                     │       agentdb.       Central KB
                     │       sqlite3         server
                     │       (local)      (shared,
                     │                     cross-project)
                     │          │           │
                     │          ▼           ▼
                     │    search-kb    (unified skill)
                     │    skill        searches all backends
                     │                   kb pull/drift
                     │
                     ▼
            (Claude only) memory/*.md
```

## Prerequisites

- `session-distillation` skill installed
- **For vector DB:** embed-server running at `host.containers.internal:9001` — **auto-started by Docker Compose via `entrypoint-wrapper.sh` in this project**, no Ollama model needed
- **For Central KB:** `kb` CLI installed (`kb` skill) + server reachable — embeddings handled by embed-server
- Scripts at `/project/tooling/scripts/{load-kb-to-memory,search-kb-memory}.py` (only needed for vector DB)
- Migration script at `/project/scripts/migrate-to-okf.py` (for legacy YAML → OKF conversion)
- Migration script at `/project/scripts/migrate-okf-v01-to-v02.py` (for OKF v0.1 → v0.2 migration, optional — manual migration guidance provided in Phase 1)
- (Pi only) `pi-hermes-memory` extension installed — manages all memory file writing

## Phase 1 — Distill (always runs)

### OKF v0.2 Format Reference

Each knowledgebase entry is an **OKF v0.2 markdown file** with YAML frontmatter. v0.2 adds provenance, trust, lifecycle, and attestation as first-class frontmatter families while keeping the format minimally opinionated.

#### Minimal example

```markdown
---
type: Decision
title: Adopt OKF v0.2 for Central Knowledge Base
description: Migrated Central KB from OKF v0.1 to v0.2
tags: [okf, central-kb, knowledge-management]
status: stable
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-21T00:00:00Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
sources:
  - id: okf-spec
    resource: https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md
    title: OKF v0.2 Specification
    author: team:knowledge-catalog
    last_modified: 2026-06-20
---

# Context
We needed a standardized format for knowledge entries with provenance and trust.

# Decision
Adopt OKF v0.2 with full provenance, trust, and lifecycle metadata.

# Consequences
All new entries use OKF v0.2 markdown format with `generated`, `verified`, `status`, and `sources` where applicable.
```

#### Required frontmatter fields

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Entry type: `Decision`, `Pattern`, `Session`, `Concept`, `Reference`, `Attested Computation`, etc. |

`type` is the only always-required key. A concept carrying just `type` is fully conformant.

#### Recommended frontmatter fields

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Human-readable display name. If omitted, consumers MAY derive a title from the filename. |
| `description` | string | One-line summary. Used by `index.md` generators, search snippets, and previews. |
| `tags` | list | YAML list of short strings for cross-cutting categorization. |
| `resource` | string | Canonical URI for the underlying asset the concept describes. Absent for abstract ideas. |

#### Provenance family (`sources`)

Records the materials a concept derives from. Each source entry carries optional credibility signals so consumers can infer trust.

```yaml
sources:
  - id: ga4-schema
    resource: https://developers.google.com/analytics/bigquery/export-schema
    title: GA4 BigQuery Export schema
    author: team:ga4-docs
    usage_count: 5000
    last_modified: 2026-05-30
usage_window: { from: 2026-06-01, to: 2026-06-30 }
```

| Sub-field | Required | Description |
|-----------|----------|-------------|
| `resource` | Yes (per entry) | URL, bundle-relative path, or scope descriptor |
| `id` | No | Stable key for per-claim attribution via footnotes |
| `title` | No | Human-readable label |
| `author` | No | Who/what produced the source (actor convention) |
| `usage_count` | No | How often the resource was exercised over `usage_window` |
| `last_modified` | No | When the source itself last changed (`YYYY-MM-DD`) |

**Per-claim attribution:** Use markdown footnotes keyed to `sources[].id`:

```markdown
The `events_` table is sharded daily as `events_YYYYMMDD`.[^ga4-schema]

[^ga4-schema]: GA4 BigQuery Export schema
```

#### Trust family (`generated`, `verified`)

`generated` records how the current content was produced. `verified` records who or what has confirmed the content against its sources.

```yaml
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified:
  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }
  - { by: process:finance-nightly, at: 2026-06-26T02:00:00Z }
```

| Field | Required | Description |
|-------|----------|-------------|
| `generated.by` | Yes (within `generated`) | Actor who produced the content |
| `generated.at` | No | ISO 8601 datetime of last meaningful change |
| `verified` | No | List of `{ by, at }` verification events. A single verifier MAY be a bare mapping (consumers treat it as a one-element list). |

**Trust tiers** (derived from `verified`, lowest to highest):
- No `verified` key ⇒ **unverified**
- `verified` by non-`human:` actors only ⇒ **machine-confirmed**
- `verified` by a `human:` actor ⇒ **human-reviewed**

#### Lifecycle family (`status`, `stale_after`)

```yaml
status: stable        # draft | stable | deprecated
stale_after: 2026-09-23   # absolute date; content is stale on/after this day
```

| Field | Values | Description |
|-------|--------|-------------|
| `status` | `draft`, `stable` (default), `deprecated` | Lifecycle stage |
| `stale_after` | `YYYY-MM-DD` | Absolute date after which content is stale |

Absent `status` ⇒ `stable`.

#### Actor convention

Fields that record identity (`generated.by`, `verified[].by`) use:

| Prefix | Example | Meaning |
|--------|---------|---------|
| `agent/` | `reference_agent/gemini-2.5-pro` | Agent or tool |
| `human:` | `human:ahormati` | Person |
| `process:` | `process:finance-nightly` | Automated process |

Consumers that classify trust key off the `human:` prefix, so producers MUST use it for hand-authored or human-confirmed content.

#### Attested Computation type

A concept of `type: Attested Computation` carries a sanctioned way to compute a value, so a consumer can confirm the agent ran the blessed computation instead of improvising.

```yaml
---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
status: stable
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
attester:
  resource: references/attesters/sql-equality.py
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
stale_after: 2026-09-23
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
---

# Computation

    SELECT SUM(amount) AS revenue
    FROM finance.recognized_revenue
    WHERE fiscal_year = @year
```

| Field | Required | Description |
|-------|----------|-------------|
| `runtime` | Yes (for this type) | How to run: `bigquery`, `postgres`, `dbt`, `python`, `Looker`, etc. |
| `parameters` | No | List of `{ name, type, required }` holes the agent may fill |
| `computation` | No | Path to a file holding the computation (instead of inline body fence) |
| `executor` | No | How the computation is run. `resource` names run instructions; `receipt` declares return fields. |
| `attester` | No | Deterministic (no-LLM) code that inspects a receipt and returns a verdict. |

#### Body

Standard markdown after the closing `---`. Conventional headings:

| Heading | Purpose |
|-----------------|--------------------------------------------------------|
| `# Schema` | Structured description of an asset's columns/fields |
| `# Examples` | Concrete usage examples, often as fenced code blocks |
| `# Computation` | The sanctioned computation of an Attested Computation |

#### Type-to-namespace mapping

| OKF Type | Directory |
|----------|-----------|
| `Decision` | `decisions/` |
| `Pattern` | `patterns/` |
| `Session` | `sessions/` |
| `Concept` | `concepts/` |
| `Reference` | `references/` |
| `Attested Computation` | `computations/` |
| *(unknown)* | lowercased type |

#### v0.1 → v0.2 migration notes

When encountering existing v0.1 entries:

1. **`timestamp` → `generated`**: Replace `timestamp: 2026-06-21T00:00:00Z` with `generated: { by: <actor>, at: 2026-06-21T00:00:00Z }`. If the original author is unknown, use `process:migration` as the actor.
2. **Body `# Citations` → `sources`**: Move citation URLs into `sources` frontmatter entries. The body `# Citations` section can remain for backward compatibility but is superseded.
3. **Add `status: stable`** to entries that are complete and reviewed.
4. **Add `verified`** where human review has occurred.
5. **Add `stale_after`** for time-sensitive knowledge (decisions, metrics).

### On Pi

Run the session-distillation workflow for **knowledgebase files only** (skip memory):

1. **Scan** the conversation for decisions, gotchas, architecture realities, user preferences, bug root causes, integration details, troubleshooting procedures, and operational risks
2. **Check existing entries** — read `knowledgebase/index.md` before writing
3. **Write knowledge base entries** — OKF markdown files for decisions, patterns, and sessions:
   - `knowledgebase/decisions/*.md` — architecture decisions with rationale and alternatives
   - `knowledgebase/patterns/*.md` — implementation patterns, troubleshooting procedures
   - `knowledgebase/sessions/*.md` — session summaries (what was done, what changed)
4. **Update index file** — `knowledgebase/index.md` (OKF bundle index with `okf_version: "0.2"`). The bundle-root `index.md` MAY carry `okf_version: "0.2"` in its frontmatter (the only place frontmatter is permitted in an `index.md`).
5. **Verify** — no duplicates, no stale entries, index counts accurate, all entries conform to OKF v0.2 frontmatter conventions

**Do NOT write memory files.** Pi's `pi-hermes-memory` extension handles `MEMORY.md`, `USER.md`, and failure tracking automatically. Writing memory here creates duplicate/conflicting entries.

### On Claude Code

Run the full session-distillation workflow including both memory and knowledgebase:

1. **Scan** the conversation (same as Pi)
2. **Check existing entries** — read `~/.claude/projects/*/memory/MEMORY.md` and `knowledgebase/index.md` before writing
3. **Write memory entries** — markdown files with YAML frontmatter:
   ```markdown
   ---
   name: descriptive-name
   description: one-line summary
   type: user | feedback | project | reference
   ---
   Content...
   ```
4. **Write knowledge base entries** — same as Pi above (OKF `.md` format)
5. **Update index files** — `MEMORY.md` and `knowledgebase/index.md`
6. **Verify** — no duplicates, no stale entries, index counts accurate

## Phase 2 — Index (DISABLED by default)

> **Gated behind `DISTILL_INDEX_ENABLED=1`.** Indexing is being redesigned and is OFF by default. Set `DISTILL_INDEX_ENABLED=1` to run it. Until the redesign lands, treat the vector DB / Central KB pipeline below as reference only.

```bash
# Opt in explicitly to run indexing (default: OFF)
if [ "${DISTILL_INDEX_ENABLED:-0}" != "1" ]; then
  echo "Indexing disabled (DISTILL_INDEX_ENABLED != 1). Skipping Phase 2."
  exit 0
fi
```

When enabled, all detected indexers run. Vector DB (local) and Central KB (shared) are independent — each serves different search needs.

### Vector DB (local)

If an embedding source is available (embed-server HTTP sidecar or Ollama), build the vector index:

```bash
python3 /project/tooling/scripts/load-kb-to-memory.py
```

This reads all `knowledgebase/{decisions,patterns,sessions}/*.md` and `*.yaml` files, generates 384-dim embeddings (embed-server HTTP sidecar preferred, Ollama fallback), and stores them in `/project/.agent/agentdb.sqlite3`. Uses `INSERT OR REPLACE` — safe to run repeatedly.

**In this project:** Docker Compose via `entrypoint-wrapper.sh` starts `embed-server.py` automatically, serving embeddings via HTTP at `host.containers.internal:9001`. **No Ollama model download needed**.

Verify after indexing:

```bash
python3 -c "
import sqlite3
db = sqlite3.connect('/project/.agent/agentdb.sqlite3')
c = db.execute('SELECT namespace, COUNT(*) FROM embeddings GROUP BY namespace')
for r in c: print(f'  {r[0]}: {r[1]}')
"
```

### Central KB (shared, cross-project)

If the `kb` CLI is available and the server is healthy, submit entries to the Central KB:

```bash
kb submit --project $CENTRAL_KB_PROJECT
```

The `kb` CLI:
- Auto-generates 384-dim embeddings via embed-server (this project) or Ollama fallback
- Pre-computes simhash to avoid server-side OverflowError (unsigned int64 → signed int64 conversion)
- Submits in batches of 5
- Reports accepted/duplicate/conflicted/error for each entry
- Server URL is auto-detected (host.containers.internal:9000) or from `CENTRAL_KB_URL` env var

**Prerequisites:** `CENTRAL_KB_PROJECT` env var must be set. If not set, Central KB indexing is skipped with a warning.

**Note:** In this project, Docker Compose via `entrypoint-wrapper.sh` starts embed-server automatically — no Ollama model download needed. The `load-kb-to-memory.py` and `search-kb-memory.py` scripts use the HTTP-based `kb_common.py` embedding pipeline.

Verify after submitting:

```bash
kb health                    # Server reachable
kb search "test" --scope $CENTRAL_KB_PROJECT  # Search works
kb pull --project $CENTRAL_KB_PROJECT       # Pull works
kb explain "topic" --scope $CENTRAL_KB_PROJECT  # Structured results → agent synthesizes
```

## Phase 3 — Search

Use the **`search-kb` skill** — it searches all available backends and the agent synthesizes results into a coherent narrative.

- **Vector DB (local):** cosine similarity search via `search-kb-memory.py`
- **Central KB (shared):** semantic + FTS search via `kb search`, structured explain via `kb explain`
- Both backends are searched when available — they cover different scopes (local vs cross-project)
- The agent synthesizes findings from all backends into a unified answer

See the `search-kb` skill for full details, pre-flight detection, and agent patterns.

### Quick reference

| Question | Command |
|----------|--------|
| Local search (all namespaces) | `python3 /project/tooling/scripts/search-kb-memory.py "<query>"` |
| Local search (decisions only) | `python3 /project/tooling/scripts/search-kb-memory.py "<query>" -n decisions` |
| Shared search | `kb search "<query>" --scope <project>` |
| Structured explain | `kb explain "<query>" --scope <project>` |
| Pull new entries from other projects | `kb pull --project <project>` |
| Check for concept drift | `kb drift --project <project>` |
| Validate OKF v0.2 bundle | `python3 -c "import sys; sys.path.insert(0,'/project/tooling/central-kb'); from app.okf import validate_okf_bundle; errors=validate_okf_bundle('/project/knowledgebase'); print(errors or '✅ OKF v0.2 conformant')"` |

## How Agents Use This

Agents treat the distill-and-index pipeline as a two-way memory system:

### Writing (Phase 1 → 2)

**On Pi (vector DB + Central KB):**
```
Agent completes work
  → distill-and-index runs (manual or PreCompact hook)
    → Pre-flight: detect legacy YAML, auto-convert to OKF v0.1
                  detect OKF v0.1 (timestamp field), migrate to v0.2
    → Phase 1: session-distillation scans conversation, writes OKF v0.2 .md files only
               with generated, verified, status, sources, stale_after as applicable
               (memory is skipped — pi-hermes-memory handles that independently)
    → Phase 2 (INDEXING, DISABLED by default): only if DISTILL_INDEX_ENABLED=1
      → 2a: load-kb-to-memory.py indexes entries into local vector DB
      → 2b: kb submit pushes entries to Central KB (cross-project sharing)
```

> **Note:** Phase 2 is OFF by default pending redesign. Until `DISTILL_INDEX_ENABLED=1` is set, only Phase 1 (distill) runs.

**On Claude Code:** Same flow, but Phase 1 also writes memory files. Central KB push still runs in Phase 2b (only when `DISTILL_INDEX_ENABLED=1`).

### Reading (Phase 3)

**Local search (vector DB):**
```
Agent starts new task
  → search-kb-memory.py "<topic>" — find relevant prior knowledge by similarity
```

**Shared search (Central KB):**
```
Agent starts new task
  → kb search "topic" --scope my-project — search across project entries
  → kb explain "topic" --scope my-project — structured view of how entries relate
  → Agent synthesizes the narrative from kb explain output (no --llm needed in-session)
  → kb pull --project my-project — pull new entries from server
  → kb drift --project my-project — check for concept drift
  → Cross-project: find decisions/patterns from other teams
```

**Important:** When an agent is in-session, `kb explain` (without `--llm`) provides structured output that the agent LLM itself synthesizes into a narrative. This is superior to `kb explain --llm` which calls a small local model — the session model is far more capable. Use `--llm` only for standalone CLI use outside an agent session.

### Concrete agent patterns

**Before implementing a feature:**
1. Local: `search-kb-memory.py "feature architecture"` — find related decisions
2. Shared: `kb search "feature architecture" --scope my-project` — find cross-project knowledge
3. Apply known constraints, avoid rejected alternatives

**When debugging a problem:**
1. Local: `search-kb-memory.py "<error message>"` — find past encounters
2. Shared: `kb search "error symptom" --scope my-project` — cross-project troubleshooting
3. Shared: `kb explain "error symptom" --scope my-project` — structured view of how entries relate, then agent synthesizes narrative
4. Trace dependency chains, look for patterns

**When a session ends (PreCompact hook):**
1. Pre-flight: detect legacy YAML → convert to OKF v0.1, then migrate v0.1→v0.2
2. Distill findings into knowledgebase (skip memory on Pi) using OKF v0.2 format with `generated`, `verified`, `status`, `sources`, `stale_after`
3. Index local: `load-kb-to-memory.py` (if embedding source available)
4. Index shared: `kb submit --project $CENTRAL_KB_PROJECT` (if Central KB available)
5. Next session picks up from where this one left off

## Auto-Run via Hook

### On Pi — standard mode

For automatic distillation before context compaction, add to `.pi/settings.local.json`:

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Pre-flight: detect legacy YAML files in knowledgebase/ and convert to OKF via python3 /project/scripts/migrate-to-okf.py; then detect OKF v0.1 files (using 'timestamp' field) and migrate to v0.2 via python3 /project/scripts/migrate-okf-v01-to-v02.py. Phase 1: distill conversation into OKF v0.2 markdown files using session-distillation (skip memory — hermes-memory handles that). Include generated, verified, status, sources, and stale_after frontmatter where applicable. Phase 2 (INDEXING) is DISABLED by default — do NOT run load-kb-to-memory.py or kb submit unless DISTILL_INDEX_ENABLED=1 is explicitly set; indexing is being redesigned.",
        "statusMessage": "Distilling session, converting legacy YAML, migrating v0.1→v0.2, indexing into vector DB, and syncing to Central KB..."
      }]
    }]
  }
}
```

### On Claude Code

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "auto",
      "hooks": [{
        "type": "agent",
        "prompt": "Run the distill-and-index skill. Pre-flight: detect legacy YAML files in knowledgebase/ and convert to OKF via python3 /project/scripts/migrate-to-okf.py; then detect OKF v0.1 files (using 'timestamp' field) and migrate to v0.2 via python3 /project/scripts/migrate-okf-v01-to-v02.py. Phase 1: distill conversation into memory/KB files using session-distillation. Use OKF v0.2 format with generated, verified, status, sources, and stale_after frontmatter where applicable. Phase 2 (INDEXING) is DISABLED by default — do NOT run load-kb-to-memory.py or kb submit unless DISTILL_INDEX_ENABLED=1 is explicitly set; indexing is being redesigned.",
        "statusMessage": "Distilling session, converting legacy YAML, migrating v0.1→v0.2, indexing into vector DB, and syncing to Central KB..."
      }]
    }]
  }
}
```

## Output

After running, confirm:

**All modes (Pi):**
1. **KB entries created** — `cat knowledgebase/index.md` (should declare `okf_version: "0.2"`)
2. **Memory untouched** — hermes-memory manages memory files independently
3. **Indexing skipped by default** — confirm Phase 2 did not run unless `DISTILL_INDEX_ENABLED=1`

**All modes (Claude Code):**
1. **Memory files written** — `ls ~/.claude/projects/*/memory/`
2. **MEMORY.md updated** — `cat ~/.claude/projects/*/memory/MEMORY.md`
3. **KB entries created** — `cat knowledgebase/index.md` (should declare `okf_version: "0.2"`)
4. **OKF v0.2 conformant** — `python3 -c "import sys; sys.path.insert(0,'/project/tooling/central-kb'); from app.okf import validate_okf_bundle; errors=validate_okf_bundle('/project/knowledgebase'); print(errors or '✅ OKF v0.2 conformant')"`

**Vector DB (if available):**
1. **Vector index populated** — `python3 -c "import sqlite3; db=sqlite3.connect('/project/.agent/agentdb.sqlite3'); print(db.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0], 'entries')"`
2. **Search works** — `python3 /project/tooling/scripts/search-kb-memory.py "test" -l 3`

**Central KB (if available):**
1. **Server healthy** — `kb health`
2. **Entries submitted** — check kb submit output for accepted count
3. **Search works** — `kb search "test" --scope $CENTRAL_KB_PROJECT`
