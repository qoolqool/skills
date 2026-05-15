---
name: search-kb
description: Search knowledgebase for relevant prior knowledge. Uses vector DB (preferred) or graphify fallback. Finds decisions, patterns, and sessions before starting new work.
allowed-tools: Bash
---

# Search Knowledgebase

Search the knowledgebase for decisions, patterns, and sessions relevant to a natural language query. Automatically uses the best available search method.

## Dual-Mode Search

| Mode | Detection | Search Method | Advantages |
|------|-----------|--------------|------------|
| **Vector DB** ✅ preferred | Ollama running + `bge-large:latest` model | `/search-kb` (cosine similarity) | Fuzzy matching of unstructured text, fast, small-LLM friendly |
| **Graphify** fallback | `graphify` importable + `graphify-out/graph.json` exists | `/graphify query`, `/graphify explain`, `/graphify path` | Relationships, communities, surprising connections, path tracing |

## Pre-flight: Detect Search Mode

Before searching, determine which method to use:

```bash
# Check for vector DB (preferred)
if curl -s http://localhost:11434/api/tags 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
models = [m['name'] for m in d.get('models', [])]
sys.exit(0 if any('bge-large' in m for m in models) else 1)" 2>/dev/null; then
  echo "SEARCH_MODE=vectordb"
else
  # Check for graphify fallback
  if python3 -c "import graphify" 2>/dev/null && [ -f graphify-out/graph.json ]; then
    echo "SEARCH_MODE=graphify"
  else
    echo "SEARCH_MODE=none"
    echo "⚠ No search available. Start Ollama with bge-large model, or install graphify (pip install graphifyy) and run /graphify . first."
  fi
fi
```

## Usage

### Vector DB mode (preferred)

```
/search-kb "<query>"                     # search all namespaces
/search-kb "<query>" -n decisions         # scope to decisions only
/search-kb "<query>" -n patterns -l 10    # patterns, top 10 results
```

If the `bge-large` model is not available, abort the search and instruct the user to verify the container startup or pull the bge-large model.

### Graphify fallback

```
/graphify query "<question>"                     # broad context (BFS)
/graphify query "<question>" --dfs                # trace a specific chain
/graphify query "<question>" --budget 1500        # cap answer at N tokens
/graphify explain "Concept Name"                  # everything about one node
/graphify path "Concept A" "Concept B"            # shortest path between two concepts
```

Graphify answers questions that cosine similarity cannot:
- **"How does X connect to Y?"** → `/graphify path`
- **"What are the cross-cutting patterns?"** → community detection + surprising connections
- **"What's the most important concept in this area?"** → god nodes + betweenness centrality
- **"What's the full context around X?"** → `/graphify explain` returns the node, all connections, and source file

Graphify is heavy for small LLMs — use it selectively.

## Namespaces (Vector DB mode)

| Namespace | Content | Use When |
|-----------|---------|----------|
| `decisions` | Architecture decisions with rationale and alternatives | Making design choices |
| `patterns` | Implementation patterns, troubleshooting procedures | Solving a specific problem |
| `sessions` | Session summaries (what was done, what changed) | Finding recent related work |

Omit `-n` to search all namespaces.

## Agent Patterns

**Before starting implementation (vector DB):**
```
/search-kb "<feature name>" -n decisions
```
Find prior decisions that constrain or inform the approach.

**Before starting implementation (graphify):**
```
/graphify query "<feature name> architecture decisions"
```
Find related decisions, their connections, and the community they belong to. Surprising connections reveal cross-cutting concerns.

**When debugging (vector DB):**
```
/search-kb "<error message or symptom>" 
```
Find gotchas, troubleshooting patterns, and past encounters with the same issue.

**When debugging (graphify):**
```
/graphify query "<error message>"
/graphify path "Error Symptom" "Root Cause"
```
Trace the dependency chain from symptom to cause.

**Before proposing architecture (vector DB):**
```
/search-kb "<design question>" -n decisions -l 10
```
See what was already decided and why alternatives were rejected.

**Before proposing architecture (graphify):**
```
/graphify explain "<design concept>"
```
See what connects to the concept, what communities it bridges, and what decisions reference it.