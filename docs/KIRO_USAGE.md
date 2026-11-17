# How Kiro built Crypt Keeper

Crypt Keeper was built in the Kiro IDE over one long session, from an empty folder to a working
app: about 2,200 lines of backend code, 1,770 lines of tests (412 tests, 31 of them
property-based), and a 760-line UI with no dependencies.

This page says what each Kiro feature did for the project, including where it did not help.

## What I used, and what I did not

| Feature | Used? |
|---|---|
| Kiro IDE (agent) | Yes, for everything |
| Specs (requirements, design, tasks) | Yes |
| Steering | Yes, four files, one of them conditional |
| Agent hooks | Yes, three |
| MCP | Built and verified, see the caveat below |
| Property-based testing | Yes |
| Kiro CLI, Kiro Web | No, so I cannot compare them with the IDE |
| Kiro Crew, custom agents, powers | No |

I left out Crew and custom agents on purpose. The Coroner (diagnosis) and the Necromancer
(rewrites) are deterministic Python modules, not LLM calls. A rewrite that comes from a model
cannot promise to be idempotent, to keep comments, or to be explainable line by line. These
can all be tested properly when the logic is plain code, and they are tested here.

## Spec-driven development

The spec is in [`.kiro/specs/crypt-keeper-core/`](../.kiro/specs/crypt-keeper-core/): 8
requirements with acceptance criteria, a design, and 13 ordered tasks (12 done at the time of
writing; the last is this polish pass).

What the spec did well:

- **Order.** Pure modules first (models, Coroner, Necromancer), then the network and subprocess
  code, then the API, then the UI. Each task ended with tests and a run against real packages.
- **Acceptance criteria exposed gaps in my own scaffold.** Requirement 2.1 says liveness depends
  on declared Python support. The first `classify_liveness` accepted that argument and ignored
  it. Requirement 2.4 says the report lists each cause of death, yet a package that was dead only
  by age produced an empty findings list. Writing one test per criterion made both visible.

What the spec did not do: it did not predict what the build taught. Four requirements and
design decisions came from running the code, not from planning, and the design had to be
rewritten once:

- Older PyPI releases list no dependencies, so the exhumer reads the sdist's packaging files
  (requirement 7.4).
- A file-count cap made `boto` look dead because the cap dropped real modules, so truncation is
  now flagged everywhere (7.3).
- A run on Python 3.11 can look healthy for a package that is dead on 3.12, so a run on the wrong
  Python can never read as success (7.2).
- The code-running endpoints needed protection from other websites (requirement 8).

Requirement 4.3 asked the smoke test to call "any public zero-argument function". Calling
`clean()` or `main()` in untrusted code is dangerous, so I amended it to read-only-looking names
and wrote down why. Compared with vibe coding: the spec kept scope and order, and the survey
scripts I used to pick demo packages were pure vibe. I kept the spec current on purpose, so it
describes the system that exists.

## Steering

[`.kiro/steering/`](../.kiro/steering/) has `product.md`, `tech.md` and `structure.md` (always on)
and `sandbox-safety.md` (loaded only when editing the sandbox, the API or `proof/`).

The rule that mattered most: **the Coroner and Necromancer are pure, with no network and no
I/O.** That one sentence is why nearly everything has property tests, and why validating the
rewriter against 1,092 real files from 20 PyPI packages took one script. The conditional file
holds the safety invariants of the code that runs untrusted packages, so they are in front of the
agent exactly when that code changes, and not in every conversation.

## Hooks

Three hooks in [`.kiro/hooks/`](../.kiro/hooks/):

- **Quick tests on save.** Runs the backend tests minus the slow real-virtualenv ones, with a
  light Hypothesis profile: 16 seconds. My first version ran the full suite on every save. Once
  the sandbox tests existed that took about 100 seconds, so I split it.
- **Full suite after each spec task.** The slow tests run here.
- **Safety review on save** (an agent hook), for the sandbox, API and `proof/` files. It sends the
  agent back to `sandbox-safety.md` and asks whether any invariant was weakened.

Caveat: Kiro reported that these hooks become active on the next session start, so during the
build I ran the same commands by hand. I have not yet observed them firing on their own.

## MCP

[`mcp-server/server.py`](../mcp-server/server.py) exposes `get_pypi_metadata` and `get_repo_tree`,
and it reuses the exact exhumer code the app uses, so Kiro and the app see the same data.

I tested it the way Kiro talks to it, over stdio against real PyPI. That found a real bug: the
installed SDK was `mcp` 2.x, which renamed `FastMCP` to `MCPServer`. The server crashed on
startup, and my own error message blamed a missing package. It now supports both versions, the
message is accurate, and a test starts the server over stdio so an SDK change cannot break it
silently.

Caveat: the build did not depend on this server. I have to add it to Kiro's MCP configuration
by hand (see `mcp-server/README.md`), and I will only claim it helped once I have used it from
Kiro to triage candidate packages.

## Property-based testing

31 Hypothesis properties cover the pure modules: dependency bumping and source rewriting are
idempotent and always produce valid output; written paths can never escape the sandbox; the
dependency validator never lets a pip option or URL through; discovery never raises on any
input; the verdict can say "resurrected" only for dead-then-alive on the target Python. The
important ones ran at 5,000 examples per property, not only the default.

Honest result: **PBT found no bug in the production code.** It found two flaws in my own test
generators (package names ending in `-`, and Hypothesis's speed check firing on a busy machine).
It did its job as a guard: the invariants held. The bugs that mattered came from elsewhere.

## What found the real bugs

Running against real packages and a real browser found what unit tests and properties did not:

1. Missing dependencies for older packages (`unittest2`, `Flask-Script` came back with none).
2. `boto` judged dead because my own file cap dropped modules.
3. A "dead" package that looked alive on the wrong Python.
4. The MCP SDK rename.
5. The UI showing a stray `false` and `[object HTMLDetailsElement]` where results should be, and
   long strings widening the page by 2,000 pixels. Only a real browser run showed these.

So the habit that paid off was: after each task, run the thing on real input, then turn what
broke into a test.

## Results

On Python 3.12.10, with the check run on the target Python itself:

| Package | Before | After |
|---|---|---|
| `attrdict` (2019) | dead: `cannot import name 'Mapping' from 'collections'` | alive (13 s) |
| `hyper` (2016) | dead; 4 tests pass, 13 error | alive; 95 pass (29 s) |
| `unittest2` (2015) | dead: no `MutableMapping` | alive; 6 pass (30 s) |
| `flask-restplus` (2019) | dead: needs a removed `werkzeug` function | still dead |

## Limits and what I would do next

- Run proof of life in a container. The current sandbox is not a security boundary.
- Convert Python 2 source. Today it is reported, not converted.
- Handle data files and C extensions, which the exhumer skips.
- Cache virtualenvs so a repeat run is faster.
