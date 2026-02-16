# FHIR Conditions Manager: System Design Decisions

## What Are We Actually Building?

Building a Python system that holds a live in-memory representation of FHIR
Condition resources, simulates two-day incremental ingestion, lets a user correct the
data (specifically removing tuberculosis references), and exposes two FastMCP (py based) tools:

1. RAG retrieval
2. Corrections.

Priorities are accuracy, latency, and monitoring.

I spent time exploring the data and thinking through each design decision. I was constantly looking up terms that I wasn't aware of and researching them. And as I went through this I split up the design into small decisions.

> I used AI to summarise my decisions and this document captures that thought process.

---

## Exploring the Data

First thing I did was dig into `conditions.json`. 105 FHIR Condition resources for one patient, spanning 2005 to 2017. Four coding systems: SNOMED CT, ICD-10-CM, ICD-9-CM, and IMO.

The data was messier than I expected:

- **14 conditions missing `clinicalStatus`**, apparently a required FHIR field.
- **2 non-medical entries** like `*NEW MEMBER` and `PROMPT AUTHORIZATION` with ICD-10 codes of `"ADMIN CODE"`. System artifacts.
- **88 conditions with durations under 24 hours.** Spinal stenosis lasting 30 minutes? Data entry issue.
- **4 vague entries**, encounter descriptions filed as conditions.
- **8 TB-related entries.** 7 called "Latent tuberculosis" (ICD-10: Z22.7), but 1 called "HX OF LATENT TB" (ICD-10: Z86.15). A text search for "tuberculosis" wouldn't catch that last one.
- **Lots of duplicates across encounters.** Lumbar spinal stenosis shows up 8+ times with different display text but the same ICD-10 code M48.061.

---

## Decision 1: Ingestion: Ingest Everything, Flag Issues, Never Discard

My first instinct was to filter out the junk. But this is medical data, you can't silently drop things. The correction mechanism is the general-purpose answer to bad data. The system should be honest about what it got, and the user decides what to fix. Quality flags at ingestion catch the issues before they pollute downstream queries.

Every condition gets checked during ingestion and flags get attached:

- `admin_code` (high) non-clinical admin entry (2 records)
- `vague_entry` (high) encounter/procedure code, not a condition (4 records)
- `missing_clinical_status` (medium) no active/resolved status (14 records)
- `inconsistent_status` (medium) has an end date but marked "active", or no end date but marked "resolved". This one I added later after noticing how many records had status/date mismatches. Ended up flagging 87 of the 105 records, which was way more than I expected. Suggests the source system wasn't rigorous about updating status when conditions resolved.
- `short_duration` (low) onset period < 24 hours (88 records)
- `missing_icd10` (low) no ICD-10 code, SNOMED only

Flags are just strings on each record, with a single static dict holding the severity/description metadata. Only 6 flag types, metadata never changes per-instance, no need for a dataclass.

---

## Decision 2: Data Representation: Simple Dict, Pre-computed Search Text

105 records. A full scan takes microseconds. `dict[str, ConditionRecord]` keyed by FHIR resource UUID, direct lookup by ID, easy to iterate. Everything stays in-memory and synchronous.

Each record gets a `searchable_text` field built at ingestion, a lowercase string with all display names and codes concatenated. Text search becomes `if query in record.searchable_text` instead of walking nested JSON at query time.

I considered secondary indexes and pre-grouping duplicates at ingestion time, but both add complexity for no real gain at this scale. Honestly I went back and forth on the pre-grouping one. It feels cleaner to have "one row per condition" in storage instead of "one row per encounter." But that means deciding at ingestion time what counts as "the same condition," and I kept finding edge cases (same ICD-10 code but different SNOMED codes, same display text but different codes, etc.). Easier to just keep the raw records and group at query time. Might revisit if it becomes a performance issue, but at 105 records it won't.

---

## Decision 3: Incremental Ingestion: Deterministic Shuffle Split

Shuffle all 105 records with a fixed seed (42), split at the midpoint (53 and 52). Both batches mix conditions from across the full date range (2005-2017). Fixed seed = reproducible.

Runs as a scripted sequence in `main.py`: load, shuffle, split, ingest batch 1, ingest batch 2, start MCP server. Both batches fully ingest before the MCP server starts, so data is ready on the first query. The monitoring dashboard tracks batches separately.

---

## Decision 4: RAG Retrieval: Three Search Params, Query-Time Grouping, No Embeddings

The MCP tool IS the retrieval layer. The LLM calls it, gets conditions back, uses that context to answer the user. With 105 records, embeddings would add latency and a dependency for zero benefit.

**Three search parameters:**

1. **`query`** (text search) matches against `searchable_text`. Handles most queries.
2. **`code`** (exact code lookup) exists because the same condition has different display text across entries but shares the same ICD-10 code. Code-based search cuts through the naming variants.
3. **`status`** (clinical status filter) narrowing parameter, usually combined with the above.

After finding matches, I group by primary ICD-10 code (or SNOMED if no ICD-10) and return one entry per group with encounter count and date range. Grouping happens at query time, not in storage, which deduplicates the noisy encounter-level data so the LLM gets clean summaries.

Output is structured text for LLM consumption, not raw JSON. I'm still not 100% sure this is the right call. Returning structured JSON would let the LLM do its own formatting and maybe give better answers in some cases. But for now the formatted text keeps the tool output readable in logs and tests, and the LLM seems to handle it fine.

---

## Decision 5: Corrections: Hybrid Matching, Soft-Delete, Audit Trail

The correction tool isn't just for TB. TB is the demo, but the tool needs to handle any condition a patient wants to remove. So I built four removal methods: `remove_by_text`, `remove_by_code`, `remove_by_id`, and `remove_by_predicate`. The first three are fully dynamic, the LLM can pass in any value.

Predicates are for the tricky cases where simple text search would miss records. TB is the clearest example: 7 entries say "Latent tuberculosis" but 1 says "HX OF LATENT TB", so text search for "tuberculosis" misses it. A named predicate combines text patterns, ICD-10 codes, and SNOMED codes so one call gets all 8. Same idea for the 2 admin entries, a predicate matching the `admin_code` quality flag removes both.

For something like "I don't have sleep apnea," text search alone works fine. Every entry has "sleep apnea" in its display text, no predicate needed.

Removals are soft-deletes. Records get marked `is_removed=True` with a reason and timestamp. In a real health system you never truly destroy records. The RAG tool just skips removed records.

---

## Decision 6: Monitoring: Logging + Dashboard, Not an MCP Tool

MCP tools are request/response, they only run when an LLM calls them. Monitoring needs to capture events as they happen. Different things.

Two layers:

1. **Python `logging`** for the event stream. Every ingestion, flag, correction, and retrieval gets a timestamped log message.
2. **`MonitoringDashboard`** tracks per-batch metrics (received, added, duplicates, errored). If a record fails to parse it's counted and logged, that's the "died on" answer. Exposed through the correction tool's `status` action so you can ask at any time what came in, what went wrong, and how the system is doing.

---

## Decision 7: MCP Server: 2 Tools

1. **`retrieve_conditions`** text, code, and/or status search. Returns grouped, formatted summaries.
2. **`correct_conditions`** remove by text, code, ID, or named predicate. Returns what was removed and current state.
