# ⚰️ Crypt Keeper

> _"Every dead package deserves a second life."_

**Crypt Keeper** exhumes abandoned Python packages from the dependency graveyard and
revives them: it diagnoses what killed them (broken deps, deprecated APIs, failing builds),
then resurrects a modern, working version — with tests to prove it breathes again.

Built for **Kiroween 2026** · Category: **Frankenstein** 🧟

---

## 🪦 The Problem

PyPI is a graveyard. Thousands of useful packages are abandoned — pinned to ancient
dependencies, broken on modern Python, unmaintained for years. Developers either fork and
suffer, or reinvent the wheel. Crypt Keeper automates the exhumation and revival.

## 🧟 What It Does

1. **Exhume** — point it at a dead PyPI package or GitHub repo.
2. **Autopsy** — the *Coroner* analyzes the cause of death: dependency rot, deprecated
   APIs, Python version incompatibility, failing imports.
3. **Resurrection** — the *Necromancer* rewrites the code to run on modern Python,
   bumps and unpins dependencies, and patches deprecated calls.
4. **Proof of Life** — generated tests confirm the revived package actually works.

## 🏗️ Architecture

```
crypt-keeper/
├── backend/          # Python (FastAPI) — analysis + revival engine
│   ├── coroner/      # diagnoses cause of death
│   ├── necromancer/  # performs the revival
│   └── api/          # REST endpoints
├── frontend/         # Web UI — the "graveyard" you resurrect from
├── mcp-server/       # MCP server wrapping PyPI/GitHub exhumation tools
└── .kiro/            # specs, steering, hooks (how this was built with Kiro)
```

## 🧪 Tech Stack

- **Backend:** Python 3.11+, FastAPI
- **Frontend:** Web (static + JS, hauntingly themed)
- **AI orchestration:** Kiro Crew (Coroner + Necromancer agents)
- **Extensibility:** MCP server for PyPI/GitHub access
- **Testing:** pytest + property-based testing (Hypothesis)

## 🚀 Getting Started

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate       # Windows PowerShell
pip install -r requirements.txt
uvicorn api.main:app --reload

# Frontend
cd frontend
# serve statically (see frontend/README once built)
```

## 🎃 How Kiro Was Used

See [`.kiro/`](.kiro/) for the specs, steering docs, and hooks that drove this build.
A full writeup lives in [`docs/KIRO_USAGE.md`](docs/KIRO_USAGE.md).

## 📜 License

MIT — see [LICENSE](LICENSE).
