# How Kiro Built Crypt Keeper

> Fill the bracketed sections in as you build. Judges score "Technological
> Implementation" on how deeply and thoughtfully you used Kiro, so be specific and
> concrete — name the spec, the hook, the agent, the moment it paid off.

## Overview
Crypt Keeper was built with Kiro using a spec-driven workflow, custom steering, agent
hooks, an MCP server, property-based testing, and a multi-agent crew. This document maps
each Kiro feature to where it shows up in the repo.

## Spec-driven development
- Spec: [`.kiro/specs/crypt-keeper-core/`](../.kiro/specs/crypt-keeper-core/)
  — requirements → design → tasks.
- [How structuring the spec changed the build vs. vibe coding. Which task caught a design
  gap before you wrote code.]

## Steering
- Docs: [`.kiro/steering/`](../.kiro/steering/) — `product.md`, `tech.md`, `structure.md`.
- [Which steering rule most changed Kiro's output — e.g. "keep Coroner/Necromancer pure"
  kept the code property-testable without re-explaining it each time.]

## Agent hooks
- [`.kiro/hooks/test-on-save.json`](../.kiro/hooks/test-on-save.json) — runs pytest on
  every backend Python save.
- [`.kiro/hooks/verify-after-task.json`](../.kiro/hooks/verify-after-task.json) — runs the
  suite after each spec task completes.
- [What these automated away and how they changed your loop.]

## MCP
- Server: [`mcp-server/server.py`](../mcp-server/server.py) exposing `get_pypi_metadata`
  and `get_repo_tree`.
- [What MCP made possible that would otherwise have been manual — sharing one PyPI/GitHub
  access layer between Kiro and the app.]

## Custom agents / Kiro Crew
- [Coroner agent (diagnosis) and Necromancer agent (revival). Which Kiro app you ran them
  in, and how splitting the work helped. Note: qualifies for Best Kiro Crew App bonus if
  you add the Crew repo URL to the submission.]

## Property-based testing
- Tests: [`backend/tests/test_revive_pbt.py`](../backend/tests/test_revive_pbt.py).
- Invariants: dependency bumping never leaves a hard pin and never drops a dependency;
  deprecated-API rewriting is idempotent.
- [Any bug PBT caught that a unit test would have missed.]

## What I'd do with more time
- [Honest next steps — judges reward self-awareness.]
