# How Kiro built Crypt Keeper

Crypt Keeper was built in the Kiro IDE over one long session, from an empty folder to a working
app: about 2,300 lines of backend code, 2,100 lines of tests (about 490 tests, 33 of them
property-based), and a 770-line UI with no dependencies.

This page says what each Kiro feature did for the project, including where it did not help.

## What I used, and what I did not

| Feature | Used? |
|---|---|
| Kiro IDE (agent) | Yes, for everything |
| Specs (requirements, design, tasks) | Yes |
| Steering | Yes, four files, one of them conditional |
| Agent hooks | Yes, three |
| MCP | Yes: a server, used from Kiro and by an agent |
| Custom agents | Yes: three, two of them used on real work |
| Skills | Yes: two |
| Powers | Authored, not yet imported into Kiro (see below) |
| Property-based testing | Yes |
| Kiro CLI, Kiro Web, Kiro Crew | No, so I cannot compare them with the IDE |

I left Crew out on purpose. The Coroner (diagnosis) and the Necromancer (rewrites) are
deterministic Python modules, not LLM calls. A rewrite that comes from a model cannot promise to
be idempotent, to keep comments, or to be explainable line by line. These can all be tested
properly when the logic is plain code, and they are tested here. So the custom agents review and
triage; they do not generate the revival code.

## Spec-driven development

The spec is in [`.kiro/specs/crypt-keeper-core/`](../.kiro/specs/crypt-keeper-core/): 8
requirements with acceptance criteria, a design, and 13 ordered tasks, all done.

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
and `sandbox-safety.md` (conditional: only when editing the sandbox, the API or `proof/`).

The rule that mattered most: **the Coroner and Necromancer are pure, with no network and no
I/O.** That one sentence is why nearly everything has property tests, and why validating the
rewriter against 1,092 real files from 20 PyPI packages took one script.

**A mistake worth knowing about.** I first wrote the conditional file's `fileMatchPattern` as a
regular expression. Kiro reads it as a glob. The file was valid, and it silently never loaded.
I found this only while reading the docs to write the custom agents, and I fixed it. A test now
checks that the pattern matches the files it guards and not the ones it should not
(`backend/tests/test_kiro_config.py`). Related, from the same docs: steering is not included
automatically for custom agents, so the agents list the steering they need under `resources`.

## Hooks

Three hooks in [`.kiro/hooks/`](../.kiro/hooks/):

- **Quick tests on save.** Runs the backend tests minus the slow real-virtualenv ones, with a
  light Hypothesis profile: 16 seconds. My first version ran the full suite on every save. Once
  the sandbox tests existed that took about 100 seconds, so I split it.
- **Full suite after each spec task.** The slow tests run here.
- **Safety review on save** (an agent hook), for the sandbox, API and `proof/` files. It sends the
  agent back to `sandbox-safety.md` and asks whether any invariant was weakened.

The hooks became active in a later session, and the safety review fired on its own while I was
preparing the public deployment. It did its job: my first edit to the API's Origin check stopped
comparing the URL scheme, which let an `http://` page pass as same-origin for an `https://` API.
The hook sent me back to `sandbox-safety.md`, I restored the scheme comparison (reading it from
the proxy's `X-Forwarded-Proto`) and added tests for it. During the earlier build I ran the same
commands by hand.

## Custom agents

Three agents in [`.kiro/agents/`](../.kiro/agents/), each with only the access it needs. The
docs say a `deny` rule always wins, so the read-only agents have both a restricted tool list and
explicit `deny` rules.

| Agent | Can | Cannot |
|---|---|---|
| `sandbox-auditor` | read the code | write files, run commands |
| `grave-digger` | read, and call the `crypt-keeper` MCP tools | write files, run commands |
| `necromancer` | edit `backend/coroner`, `backend/necromancer`, `backend/tests`; run pytest and read-only git | touch the sandbox, API, UI, steering or hooks; `git push`; delete files |

I created the files by hand in the IDE, which is the documented route, and ran two of them by
name from the same session. I have not used the CLI to run an agent. Where each helped:

**`sandbox-auditor` reviewed the code that runs untrusted packages.** It read 11 files and
reported 12 findings with severities, file and line, an exploit story and a fix. I did not accept
that on trust. I checked two myself:

- *Body-size limit bypassed by chunked requests.* **Confirmed.** A 5 KB body with a
  `Content-Length` header got the expected 413. The same body sent chunked, with no
  `Content-Length`, passed the guard.
- *The allow-list approves a package name, not its dependencies.* **A real design risk, but not
  exploitable today.** I walked the dependency graph of the four listed packages: 15 projects,
  all of them exist on PyPI, and only the four listed ones are abandoned.

**Four of the findings are now fixed**, each with tests that I checked by breaking the fix on
purpose (11 deliberate breakages, all caught):

| Finding | Fix |
|---|---|
| Body-size limit bypassed by chunked requests | Counts the bytes actually received and stops reading at the limit; a malformed `Content-Length` is a 400. Checked on a real server over a socket. |
| Allow-list fails open | Every ambiguous setting fails closed: a missing list on the public image, a blank list and a typo in the public flag all mean "nothing runs". Only an explicit `*` allows everything. Checked in the real image with a blank list. |
| No cap on archive entries or unpacked size | Refused from the headers before any data is read; checked against real large packages so legitimate ones still pass. |
| Cloud credentials reachable from the sandboxed child | The deploy steps create a runtime service account with no roles. Written from the docs and **not tested**, since I have no cloud account here. |

Fixing them taught three things:

- The safety hook fired on my own new code. `is_public()` first only recognised a few "true"
  spellings, so a typo such as `CRYPT_KEEPER_PUBLIC=enabled` would have meant "local" and an unset
  list would have meant "run anything", the same fail-open I was removing. The hook sent me back to
  the invariant; any ambiguous value now counts as public.
- A test of the fix failed in an informative way: the chunked 413 came back as a generic 400.
  FastAPI re-labels errors raised while it reads a body, and Starlette's `BaseHTTPMiddleware` wraps
  them in an `ExceptionGroup` that FastAPI does not recognise. I confirmed that by capturing the
  exception, then rewrote the request guard as plain ASGI middleware and raised FastAPI's own
  `HTTPException`.
- I twice launched a command in the same batch as the command it depended on (a smoke test next to
  the command that stopped the server, a container start next to the image rebuild). The first
  produced a false "connection refused" that looked like a crash; I traced it before believing it.

**Eight findings are still open**, none of them tested yet: a child process that survives the
sandbox's timeout, no disk or memory limit for the child, the allow-list approving a package name
but not its dependencies (hash-pinned lock files would fix that), server paths in sandbox error
text, truncated or undecodable source not downgrading the verdict, the `GITHUB_TOKEN` hint,
test paths passed to pytest without `--`, and loose worst-case time limits.

**`grave-digger` triaged five packages through MCP.** Its MCP server is declared inside its own
file, so the agent works in any clone without anyone editing `mcp.json`. I then ran Crypt Keeper
on all five and compared:

| Package | Prediction | Real result on Python 3.12.10 |
|---|---|---|
| `attrdict` | likely revivable | resurrected |
| `hyper` | not automatically revivable | **resurrected** (the prediction was wrong) |
| `docopt` | likely revivable | already alive |
| `flask-restplus` | not automatically revivable | still dead |
| `mox3` | unknown | already alive (imports; its 2 tests error) |

Three right, one wrong, one honest "unknown". The wrong one is the point of the skill's rule:
metadata can suggest a blocker (here, upper bounds on dependencies) that does not stop the
package, so only the proof-of-life verdict counts.

**`necromancer` is created and checked, but I have not used it for real work yet.** I do not
claim an impact for it.

## Skills and the power

- [`add-removed-api`](../.kiro/skills/add-removed-api/SKILL.md): the recipe for adding a removed
  API to the Coroner and, if it is a true drop-in, to the rewriter. It names the real tables and
  tests, and says to take removal versions from the CPython release notes, not from memory.
- [`triage-abandoned-package`](../skills/triage-abandoned-package/SKILL.md): how to read the MCP
  output with the Coroner's own thresholds.
- **The power** is a [`plugin.json`](../plugin.json) in the Agent Plugins format, with an
  `mcp.json` and the triage skill, at the repository root so the repository can be imported as one.
  **I have not imported it into Kiro.** I cannot confirm that its relative server path resolves
  after install, so treat it as authored, not proven.

These files rot easily, so [`test_kiro_config.py`](../backend/tests/test_kiro_config.py) (38
tests) checks them: agent and skill formats, that every resource exists and fits Kiro's limits
(32 files, 100,000 characters), that the read-only agents really are read-only, that the necromancer's
write and deny lists do not overlap, that the skills name symbols and tests that exist and state
the thresholds the Coroner really uses, and that each hook matcher fires on the files it is meant
for. I checked the tests by breaking the config seven ways (including re-introducing the regex-as-glob
bug); each break was caught, and I restored every file.

## MCP

[`mcp-server/server.py`](../mcp-server/server.py) exposes `get_pypi_metadata` and `get_repo_tree`,
and it reuses the exact exhumer code the app uses, so Kiro and the app see the same data.

I tested it over stdio against real PyPI, which found a real bug: the installed SDK was `mcp`
2.x, which renamed `FastMCP` to `MCPServer`. The server crashed on startup, and my own error
message blamed a missing package. It now supports both versions and a test starts the server
over stdio so an SDK change cannot break it silently.

I then used it from Kiro: asking for `attrdict`'s last release returned `2019-02-01`, declared
Python `2.7`, dependency `six`. The `grave-digger` agent uses the same server. The build itself
did not depend on it.

## Property-based testing

33 Hypothesis properties cover the pure modules: dependency bumping and source rewriting are
idempotent and always produce valid output; written paths can never escape the sandbox; the
dependency validator never lets a pip option or URL through; discovery never raises on any
input; the verdict can say "resurrected" only for dead-then-alive on the target Python. The
important ones ran at 5,000 examples per property, not only the default.

Honest result: **PBT found no bug in the production code.** It found two flaws in my own test
generators (package names ending in `-`, and Hypothesis's speed check firing on a busy machine).
It did its job as a guard: the invariants held. The bugs that mattered came from elsewhere.

## What found the real bugs

Running against real packages, a real browser, the docs and a reviewer found what unit tests and
properties did not:

1. Missing dependencies for older packages (`unittest2`, `Flask-Script` came back with none).
2. `boto` judged dead because my own file cap dropped modules.
3. A "dead" package that looked alive on the wrong Python.
4. The MCP SDK rename.
5. The UI showing a stray `false` and `[object HTMLDetailsElement]` where results should be, and
   long strings widening the page by 2,000 pixels. Only a real browser run showed these.
6. The conditional steering file that never loaded (found by reading the docs).
7. A body-size limit that chunked requests skip (found by the auditor, confirmed by a test).
8. A fail-open flag in my own fix for the allow-list (caught by the safety hook).
9. A 413 that FastAPI reported as a 400, because of how `BaseHTTPMiddleware` wraps errors (caught
   by a failing test of the fix).

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

- Fix the auditor's eight remaining findings, starting with the process that survives the sandbox's
  timeout and the missing disk and memory limits.
- Run proof of life in a container with real isolation. The current sandbox is not a security boundary.
- Import the power into Kiro and use the `necromancer` agent on a real change.
- Convert Python 2 source. Today it is reported, not converted.
- Handle data files and C extensions, which the exhumer skips.
