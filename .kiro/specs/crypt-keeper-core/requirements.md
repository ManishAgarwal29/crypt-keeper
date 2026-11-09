# Requirements: Crypt Keeper Core

## Introduction
Crypt Keeper revives abandoned Python packages. This spec covers the core end-to-end
flow: a user supplies a dead package, the system diagnoses why it's dead (autopsy), and
produces a revived, modern version with tests that prove it runs (proof of life). The
feature set here is what gets demoed in the <3 minute Kiroween video.

## Requirements

### Requirement 1: Exhume a package
**User Story:** As a developer, I want to point Crypt Keeper at a dead PyPI package or
GitHub repo, so that it can fetch the source and metadata for analysis.

#### Acceptance Criteria
1. WHEN the user submits a valid PyPI package name THEN the system SHALL fetch its latest
   release metadata and source distribution.
2. WHEN the user submits a GitHub repo URL THEN the system SHALL fetch the repository's
   source tree.
3. IF the package/repo cannot be found THEN the system SHALL return a clear error naming
   what was searched and where.
4. WHEN metadata is fetched THEN the system SHALL record last-release date, declared
   Python versions, and declared dependencies.

### Requirement 2: Autopsy (cause of death)
**User Story:** As a developer, I want a diagnosis of why the package is broken, so that I
understand what revival requires.

#### Acceptance Criteria
1. WHEN a package is exhumed THEN the Coroner SHALL classify its liveness (alive /
   dormant / dead) based on last-release age and declared Python support.
2. WHEN dependencies are analyzed THEN the Coroner SHALL flag each dependency that is
   unmaintained, yanked, or pinned to an incompatible version.
3. WHEN source is analyzed THEN the Coroner SHALL detect usage of deprecated or removed
   standard-library APIs for the target Python version.
4. WHEN the autopsy completes THEN the system SHALL produce a structured report listing
   cause(s) of death, each with a severity and a human-readable explanation.

### Requirement 3: Resurrection (revival)
**User Story:** As a developer, I want a revived version of the package that runs on
modern Python, so that I can actually use it.

#### Acceptance Criteria
1. WHEN revival runs THEN the Necromancer SHALL unpin or bump dependency constraints to
   versions compatible with the target Python.
2. WHEN deprecated APIs are detected THEN the Necromancer SHALL rewrite them to modern
   equivalents where a known mapping exists.
3. WHEN a transform cannot be applied automatically THEN the system SHALL record it as a
   "manual revival needed" item rather than silently skipping it.
4. WHEN revival completes THEN the system SHALL produce a diff between the dead and
   revived source.

### Requirement 4: Proof of life (tests)
**User Story:** As a developer, I want evidence the revived package works, so that I trust
the result.

#### Acceptance Criteria
1. WHEN revival completes THEN the system SHALL attempt to import the revived package and
   report success or the exact import error.
2. WHEN the original package ships tests THEN the system SHALL run them against the revived
   version and report pass/fail counts.
3. IF no tests exist THEN the system SHALL generate at least a smoke test that imports the
   top-level module and calls any public zero-argument function.

### Requirement 5: Haunted web UI
**User Story:** As a user, I want a polished, spooky interface, so that the experience is
memorable and easy to follow.

#### Acceptance Criteria
1. WHEN the app loads THEN the UI SHALL present a "graveyard" entry point with a single
   input for package name or repo URL.
2. WHEN an autopsy completes THEN the UI SHALL render the report as an "autopsy report"
   with severity indicators.
3. WHEN revival completes THEN the UI SHALL show a before/after diff and the proof-of-life
   result.
4. The UI SHALL remain readable and accessible (sufficient contrast, keyboard-navigable
   controls) despite the themed styling.

### Requirement 6: MCP exhumation tools
**User Story:** As a builder, I want PyPI/GitHub access exposed through MCP, so that Kiro
and the app share one extensible access layer.

#### Acceptance Criteria
1. The MCP server SHALL expose a tool to fetch PyPI package metadata by name.
2. The MCP server SHALL expose a tool to fetch a repository file tree by URL.
3. WHEN a tool is called with invalid input THEN it SHALL return a structured error, not
   crash.
