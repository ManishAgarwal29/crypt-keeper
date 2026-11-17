# Implementation Plan: Crypt Keeper Core

- [x] 1. Project skeleton and shared models
  - Create `backend/` package layout, `requirements.txt`, and empty module files.
  - Define all Pydantic models in `backend/models.py` (`PackageRef`, `PackageSnapshot`,
    `Finding`, `AutopsyReport`, `RevivalResult`, `ProofOfLife`).
  - _Requirements: 2.4, 3.4, 4.1_

- [x] 2. Coroner: liveness classification
  - Implement `classify_liveness` (pure) with configurable age thresholds.
  - Unit tests for alive/dormant/dead boundaries.
  - _Requirements: 2.1_

- [x] 3. Coroner: dependency analysis
  - Implement `analyze_dependencies` flagging unmaintained/yanked/incompatible deps.
  - Unit tests with fixture dependency sets.
  - _Requirements: 2.2_

- [x] 4. Coroner: deprecated API detection
  - Implement `detect_deprecated_apis` using a known-deprecations table.
  - Unit tests covering at least 3 real removed/deprecated stdlib APIs.
  - _Requirements: 2.3_

- [x] 5. Coroner: assemble autopsy report
  - Implement `run_autopsy` composing the above into an `AutopsyReport`.
  - _Requirements: 2.4_

- [x] 6. Necromancer: dependency bumping
  - Implement `bump_dependencies` (pure). Record applied vs manual.
  - Property tests: no pin below floor; idempotent.
  - _Requirements: 3.1, 3.3_

- [ ] 7. Necromancer: deprecated API rewriting
  - Implement `rewrite_deprecated` with a mapping table. Record manual items.
  - Property tests: idempotent; never drops a module path.
  - _Requirements: 3.2, 3.3_

- [ ] 8. Necromancer: assemble revival + diff
  - Implement `revive` producing `RevivalResult` with per-file diffs.
  - _Requirements: 3.4_

- [x] 9. Exhumer + MCP server
  - Implement MCP tools `get_pypi_metadata` and `get_repo_tree`.
  - Implement exhumer orchestration consuming those tools; mock in tests.
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 6.1, 6.2, 6.3_

- [ ] 10. Proof of life runner
  - Import revived package in an isolated subprocess; run shipped tests or a generated
    smoke test; return `ProofOfLife`.
  - _Requirements: 4.1, 4.2, 4.3_

- [ ] 11. API layer
  - Wire `POST /exhume`, `/autopsy`, `/resurrect`, `/proof-of-life`, and the combined
    `/revive` endpoint. Typed responses + themed error handling.
  - _Requirements: 1.3, 2.4, 3.4, 4.1_

- [ ] 12. Haunted web UI
  - Build `index.html`, `style.css`, `app.js`: graveyard input, autopsy report view,
    before/after diff, proof-of-life result. Keep it accessible.
  - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [ ] 13. Demo polish and Kiro usage writeup
  - End-to-end pass on 2-3 real dead packages for the video.
  - Fill in `docs/KIRO_USAGE.md`.
  - _Requirements: all (integration)_
