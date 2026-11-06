# ADR 0015: Native structured outputs: always `method="json_schema"`

**Status:** Accepted · 2026-10-25 (the M0 check)

## Decision
Every structured model call uses `with_structured_output(schema, method="json_schema")`, which sends Claude's native `output_config.format`. The M4 LLM gateway is the single place this is set.

## Why
The M0 check (langchain-anthropic 1.7.6) found that the default is `method="function_calling"`, which forces a tool call. Current Sonnet and Opus models reject forced tool choice. The library detects them and falls back to an unforced tool call, which can come back without a valid object. Native structured outputs guarantee the response matches the schema.

## Rejected
- **The default `function_calling`:** correct only on models that still accept forced tool choice.
