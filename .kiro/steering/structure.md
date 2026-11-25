# Project Structure: Crypt Keeper

```
crypt-keeper/
├── backend/
│   ├── api/
│   │   ├── main.py          # FastAPI app + routes
│   │   ├── exhumer.py       # PyPI/GitHub fetching (network)
│   │   └── sandbox.py       # runs revived code in a throwaway venv (subprocess)
│   ├── coroner/
│   │   └── autopsy.py       # cause-of-death analysis (pure, testable)
│   ├── necromancer/
│   │   ├── revive.py        # revival assembly + dependency bumping (pure, testable)
│   │   ├── rewriter.py      # source-preserving API rewrites (pure, testable)
│   │   ├── export.py        # patch + revived sources + report as a zip (pure, testable)
│   │   └── triage.py        # static triage and change detection for the Undertaker (pure)
│   ├── proof/
│   │   └── discovery.py     # what to import/test/smoke-call (pure, testable)
│   ├── models.py            # shared Pydantic models
│   ├── requirements.txt
│   └── tests/
│       ├── test_autopsy.py
│       └── test_revive_pbt.py   # property-based tests
├── frontend/
│   ├── index.html           # the graveyard UI
│   ├── style.css            # haunted theme
│   └── app.js               # talks to the backend API
├── mcp-server/
│   └── server.py            # MCP tools: fetch PyPI metadata, repo files
├── undertaker/              # nightly watchlist triage (run by a Kiro Crew cron job); script + state, no logic
├── benchmark/               # candidate list and results for the success-rate benchmark
├── scripts/                 # smoke_test.py, benchmark.py, review_dependencies.py
├── docs/
│   └── KIRO_USAGE.md        # the "how Kiro was used" writeup for judges
└── .kiro/
    ├── steering/            # product, tech, structure (this file)
    ├── specs/               # requirements → design → tasks
    └── hooks/               # automation
```

## Module boundaries
- `coroner/` and `necromancer/` are pure logic: no FastAPI imports, no network. This
  keeps them unit- and property-testable.
- `api/` is the only layer that orchestrates and touches the network/LLM.
- `mcp-server/` is standalone and talks to PyPI/GitHub.
- `undertaker/` and `scripts/` orchestrate and may touch the network; any decision they make lives in a pure,
  tested module (`necromancer/triage.py`, the functions in `scripts/benchmark.py`), not inline.
- Nothing in the export or the Undertaker executes package code. Only `api/sandbox.py` does.
- `frontend/` never contains business logic — it renders autopsy reports and revival results.

## Naming
Themed names are encouraged for product-facing concepts (Coroner, Necromancer, autopsy,
proof-of-life) but code identifiers stay descriptive enough to read without the theme.
