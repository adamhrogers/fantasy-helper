# ADR 0020: Low-latency reads: computed ahead, few tools, same region

**Status:** Accepted · 2026-11-06

## Decision
Agents, the phone app and the future chat read **pre-computed results**, never raw tables and never live computation:

1. **Read models.** Each run stores its FactSheet per league (`fact_sheets`, append-only, the replay unit) and a small per-league `league_player_values` table (league-scored value, games and startable games this week, value over replacement, injury status, roster status). The table is replaced each run, so it's a rebuildable cache, not evidence. Every hot read is one indexed lookup into a small table.
2. **Few, coarse, batched tools.** MCP tools return pre-joined, compact, ID'd results (for example `get_player_cards([ids])`), not one tool per table. The FactSheet should let the nightly report need **zero** tool calls, and a test holds it to a token budget.
3. **Our own agents call the MCP server in-process,** over the in-memory transport. stdio and streamable HTTP are for outside clients only. The tool definitions are the same either way.
4. **Same region, open connections.** Supabase prod lives in `us-east-1` (same as dev) and Cloud Run in `us-east4`, both in Northern Virginia. A long-running process (the API, a multi-step pipeline) holds one pooled engine; one-shot CLI commands keep their single connection.
5. **Every hot query is an index lookup**, and each tool call runs one query.
6. **Council prompts put shared content first:** shared rules and the FactSheet, then each agent's own instructions. Agents on the same model and the same tool list then reuse one cached prefix.

## Why
Measured on 2026-11-06 against Supabase dev from Adam's Mac: hot queries take 0.3–2.7 ms on the server, but each round trip costs about 34 ms and a new connection about 600 ms. In an agent, each tool call adds a full model round trip of about 1–3 seconds. Latency therefore comes from model round trips, cold starts and connections, not from the database. A synthetic full-season load (720,000 projection rows) kept every hot query under 5 ms. At five seasons (3.6 million rows) two queries grew to about 25 ms until composite indexes brought them back down, so index shape matters as history grows.

## Rejected
- **A cache tier (Redis or similar), a vector database or a second database:** cost and moving parts with no measurable gain at this size.
- **Tools that mirror tables:** more tool calls means more model round trips.
- **The stdio MCP transport in the pipeline:** it starts a Python process and opens a new connection on every run.
- **An always-warm Cloud Run instance now:** it costs money every month and only matters for chat. Decide when chat ships.

## Consequences
ADR 0003 stands (one read-only MCP server); only its transport for our own agents changes. Migration 0003 adds the read-model tables and composite indexes. Chat inherits all of this; what's left for chat is cold starts, streaming and an intraday injury refresh.
