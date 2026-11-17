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
3. IF no tests exist THEN the system SHALL generate a smoke check that imports every top-level
   module and calls public zero-argument functions that look read-only (names such as `get_*`,
   `is_*`, `version`). _Amended: the original wording said "any public zero-argument function".
   Calling `clean()` or `main()` in untrusted code can do real damage, so the set is limited to
   read-only-looking names._

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

### Requirement 7: Honest verdicts
_Added during the build. Real runs showed that a run on the wrong Python, or on an incomplete
source tree, can look like success or failure for the wrong reason._

**User Story:** As a developer, I want the verdict to say what was actually proven, so that I do
not trust a result that does not apply to my target Python.

#### Acceptance Criteria
1. WHEN the original and the revived package are both checked THEN the system SHALL return a
   verdict of `resurrected`, `improved`, `already_alive`, `still_dead`, `regressed` or
   `unverified`, with a one-line reason.
2. IF the check ran on a Python other than the target THEN the system SHALL NOT report success
   (`unverified`), because a package that imports on 3.11 can still fail on 3.12.
3. IF the exhumed source was cut short by size or count limits THEN the snapshot SHALL be marked
   truncated and the autopsy and the proof-of-life log SHALL say so.
4. WHEN PyPI metadata lists no dependencies THEN the exhumer SHALL read them from the sdist's own
   packaging files before concluding the package has none.

### Requirement 8: Safe execution and API
_Added during the build. Proof of life runs a package's own code._

**User Story:** As a user, I want proof of life to run package code with the fewest risks the
design can offer, and I want other websites unable to trigger it.

#### Acceptance Criteria
1. The sandbox SHALL install dependencies wheel-only, reject pip options and URLs in dependency
   strings, validate every written path, scrub the environment, apply a timeout to every step and
   kill the whole process tree on timeout.
2. The documentation SHALL state that the sandbox is not a security boundary.
3. The API SHALL refuse non-JSON POSTs, POSTs from foreign origins, unknown Host headers and
   oversized bodies, and SHALL limit concurrent sandbox runs.
4. The system SHALL report progress while running (stage start and finish with timings), so the
   UI shows real progress instead of an indeterminate wait.
