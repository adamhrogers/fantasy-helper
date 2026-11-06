# ADR 0002: Ports and adapters, with layers enforced by import-linter

**Status:** Accepted · 2026-10-16

## Decision
One Python package with strict internal layers (docs/architecture.md §3). `domain` sits at the center; each source gets an adapter that maps its data into the domain; CLI, MCP and HTTP are thin interfaces at the edges. import-linter contracts in `pyproject.toml` fail CI when a layer imports upward, when the engine touches storage or I/O libraries, or when the brain reaches past FactSheets and MCP tools.

## Why
ESPN's API is unofficial and can change without notice; when it does, only its adapter should change. Linted contracts turn "models never invent a stat" and "the engine is pure" from conventions into build failures.

## Rejected
- **A multi-package uv workspace:** more ceremony than one person on a four-week clock needs. Splitting later is mechanical.
- **Unenforced conventions:** they erode under deadline pressure.

## Consequences
Every top-level package exists from M0 (docstring only) so the contracts cover it. A new top-level package must be added to the contracts.
