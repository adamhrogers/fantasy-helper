# ADR 0003: One read-only MCP server over our own data model

**Status:** Accepted · 2026-10-15 (decided in the spec)

## Decision
A single MCP server exposes our data model (roster, free agents, projections, watchlist, takes, news, call history, `evaluate_move`) to every agent and, later, to chat. Its tools are read-only in v1.

## Why
Pulling and cleaning platform data stays plain, tested code that runs the same way every night; the MCP layer only reads the result. Read-only tools mean the app never acts on a league, registry writes stay deterministic (pipeline code writes them after guardrails), and a prompt injection in news text has nothing to do damage with.

## Rejected
- **One MCP server per platform:** duplicates tools and leaks platform differences into the agents.
- **Write tools for agents:** "log before the outcome" would depend on model behavior.

## Consequences
**Update, 2026-11-06 (ADR 0020):** our own agents (pipeline and chat API) reach the server in-process, over the in-memory transport; stdio and streamable HTTP are for outside clients. Tools are few, coarse and batched (for example `get_player_cards([ids])`) rather than one per table, because every tool call costs a model round trip.
