# ADR 0001: Python, with LangGraph and LangChain for agents and LangSmith for tracing

**Status:** Accepted · 2026-10-15 (decided in the spec)

## Decision
The backend is Python. Agents are built on LangGraph and LangChain, every model call is traced in LangSmith, and a Claude model is the single-model baseline.

## Why
LangGraph gives full control over who debates whom and in what order, works with several model providers (the Skeptic must come from a different family than the Analyst), connects to MCP through `langchain-mcp-adapters`, and traces into LangSmith with no extra code. It's also the stack this project is meant to demonstrate.

## Rejected
- **CrewAI:** fastest to prototype, but less control over the flow.
- **OpenAI Agents SDK:** OpenAI-first, which works against the multi-model goal.
- **Microsoft Agent Framework / AutoGen:** heavier; worth reading for debate patterns.
- **PydanticAI:** not adopted as the framework, but its idea of validating every agent output against a schema is (ADR 0004).
