# FHIR Conditions Manager

In-memory FHIR Condition manager with two MCP tools: RAG retrieval and user corrections. Ingests 105 conditions in two simulated batches, flags data quality issues, and lets a patient correct their records (e.g. removing tuberculosis entries).

<img width="617" height="645" alt="image" src="https://github.com/user-attachments/assets/28fe4db8-78ca-4648-a0e8-40c777b38aea" />

See [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md) for the full design rationale.

## Setup

```bash
uv sync
```

## Run

```bash
uv run python -m fhir_conditions_manager.main
```

This starts the MCP server on stdio. Connect it to any MCP-compatible LLM client (e.g. Cursor, Claude Desktop).

## MCP Tools

- **`retrieve_conditions`** — search by text, code, and/or clinical status
- **`correct_conditions`** — remove conditions by text, code, ID, or named predicate (e.g. `"tuberculosis"`)
