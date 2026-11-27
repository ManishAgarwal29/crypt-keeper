# Product: Crypt Keeper

Crypt Keeper diagnoses why an abandoned Python package no longer runs on modern Python, and revives it
where it safely can. It is a Kiroween 2026 submission in the
**Frankenstein** category — stitching together PyPI/GitHub archaeology, AST-based static
analysis, source-preserving code rewriting, and sandboxed execution into one app that brings
dead code back to life. The Coroner and Necromancer are deterministic, not LLM-driven, so every
change is explainable and testable.

## Who it's for
Developers who depend on (or want to depend on) an abandoned Python package and would
rather revive it than fork-and-suffer or rewrite from scratch.

## Core user journey
1. User provides a dead package name or repo URL.
2. **Coroner** diagnoses the cause of death (autopsy report).
3. **Necromancer** produces a revived, modern version.
4. Generated tests prove the revival works (proof of life).

## Non-goals
- Not a general-purpose refactoring tool. Scope is "make a dead package run on modern Python."
- Not a package registry. We revive, we don't host.

## Theme & voice
Halloween/necromancy throughout: "exhume", "autopsy", "resurrection", "proof of life",
"graveyard". The theme must enhance clarity, never obscure it. Error messages can be
spooky but must still tell the user what actually went wrong.
