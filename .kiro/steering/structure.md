# Project Structure: Crypt Keeper

```
crypt-keeper/
├── backend/
│   ├── api/
│   │   └── main.py          # FastAPI app + routes
│   ├── coroner/
│   │   └── autopsy.py       # cause-of-death analysis (pure, testable)
│   ├── necromancer/
│   │   └── revive.py        # revival transforms (pure, testable)
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
- `frontend/` never contains business logic — it renders autopsy reports and revival results.

## Naming
Themed names are encouraged for product-facing concepts (Coroner, Necromancer, autopsy,
proof-of-life) but code identifiers stay descriptive enough to read without the theme.
