# How Kiro built Crypt Keeper

Crypt Keeper was built in the Kiro IDE over one long session, from an empty folder to a working
app: about 3,300 lines of backend code, 3,900 lines of tests (about 970 tests, 50 of them
property-based), 590 lines of scripts, and an 830-line UI with no dependencies.

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
| Powers | Yes: imported from a folder, and its tool and skill used |
| Property-based testing | Yes |
| Kiro CLI, Kiro Web | No, so I cannot compare them with the IDE |
| Kiro Crew | **Designed for, not run.** The nightly Undertaker script is built and tested; the Crew job that would schedule it is written down in `undertaker/README.md` but I have not created or run it |

The revival itself does not use Crew. The Coroner (diagnosis) and the Necromancer (rewrites) are
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
  all of them exist on PyPI, and only the four listed ones are abandoned. (Now partly closed, see
  the table below.)

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

**The other eight findings are fixed too.** I wrote the tests first and ran them against the old
code: 20 of the 30 new sandbox tests failed, which confirmed each finding was real. Then I fixed
them. Every fix was checked by breaking it on purpose (29 deliberate breakages, run in the Linux
container so the Linux-only tests count).

| Finding | Fix |
|---|---|
| A process the package starts survives the run | The process tree is killed when every step ends, not only on timeout. On Linux a per-run marker in the environment also lets the server find and kill daemons that left the process group with `setsid`. A package that both calls `setsid` and wipes its own environment can still escape; the container is the real boundary. |
| No output, disk, file-size, memory or process limits | Output over 10 MB or a run directory over 1 GB stops the step. On Linux each child also gets limits on core dumps, file size (256 MB), process count and address space (3 GB, set with `CRYPT_KEEPER_CHILD_MEM_MB`, `0` turns it off). The limits are applied by a small fixed launcher that receives them as data. |
| The allow-list approves a package name, not its dependencies | On a public host, each listed package has a reviewed list of dependency names in `backend/proof/approved_dependencies.json`. A dependency that is not on it, or a package with no entry, is not run. `scripts/review_dependencies.py` prints what a person has to read before writing an entry. |
| Truncated or undecodable source does not lower the verdict | A file that is not UTF-8, or that clashes with another path, now marks the snapshot truncated, as does GitHub's own `truncated` flag. A failed run on truncated source is `unverified`, never `still_dead` or `regressed`. The top-level directory is stripped only when every entry shares it, so wheels keep their paths. |
| Server paths and raw exceptions in error text | Errors are generic and the details go to the server log. The temp directory, the interpreter path and the home directory are replaced with `<sandbox>`, `<python>` and `~` in everything a client receives. |
| The rate-limit message pushes `GITHUB_TOKEN` | It only mentions the token when the server is not in public mode. |
| Test paths passed to pytest without `--` | `--` now comes before them. |
| Loose time limit; probes on every request; unbounded exhumes | Each step gets only the time left in a 600 s budget (below the host's 900 s timeout). Interpreter lookups are cached per major.minor and never probe implausible versions. At most 8 exhumes run at once (503 when busy). |

What is **not** covered, so nobody reads more into this than is there:
- The reviewed list covers the direct dependencies. pip still resolves their dependencies at run
  time to the latest release, so a malicious new release of a transitive dependency would be
  installed. Hash-pinned lock files would close that and are not done.
- The resource limits are Linux only. On Windows only the output, disk and time limits apply.
- The Linux-only tests run in the project's container (`docker run ... python:3.12-slim`), not in
  the normal Windows run, where they are skipped.
- The service-account change and Cloud Run streaming are still untested, as before.
- Two of my first tests passed against deliberately broken code (the tree kill and the per-step
  sweep each hid behind the other one, and the implausible-target test never gave the probe
  anything to find). The mutation run showed it, and I added tests that isolate each mechanism.

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
  [`mcp.json`](../mcp.json) and the triage skill, at the repository root so the repository can be
  imported as one. It was imported with Powers panel, Add Custom Power, Import power from a folder.
  What happened:
  - My first `mcp.json` was **invalid**: it lacked `$schema` and `"type": "stdio"`, which the Agent
    Plugins schema requires. I found that by reading the schema, not from my own test, which only
    checked server names and was too loose. The test now follows the schema, and I confirmed that it
    fails on the old file.
  - The schema also defines `cwd: "${PLUGIN_ROOT}"`, which pins the working directory so the relative
    server path does not depend on the open workspace.
  - Kiro registered it as `kiroween2026`, the folder name, not the manifest's `crypt-keeper`, and
    copied the whole repository into `~/.kiro/powers/installed/`.
  - Calling the power's `get_pypi_metadata` tool for `attrdict` returned live data (version 2.0.1,
    released 2019-02-01, Python 2.7 only), so the server starts from the installed copy and the path
    resolves. The triage skill loads through the power as well.
  - For this format Kiro runs the server itself; it does not appear in `mcp.json`.

These files rot easily, so [`test_kiro_config.py`](../backend/tests/test_kiro_config.py) (44
tests) checks them: agent and skill formats, the power's manifest and MCP config against the Agent
Plugins schemas, that the power's server starts the way its manifest says, that every resource exists and fits Kiro's limits
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

39 Hypothesis properties cover the pure modules: dependency bumping and source rewriting are
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
10. A power manifest (`mcp.json`) that broke the Agent Plugins schema, which my own test had passed
    (found by reading the schema while importing the power).



So the habit that paid off was: after each task, run the thing on real input, then turn what
broke into a test.

## Results

On Python 3.12.10, with the check run on the target Python itself:

| Package | Before | After |
|---|---|---|
| `attrdict` (2019) | dead: `cannot import name 'Mapping' from 'collections'` | alive (13 s) |
| `hyper` (2016) | dead; 4 tests pass, 13 error | alive; 95 pass, 8 fail, 5 error (19 s) |
| `unittest2` (2015) | dead: no `MutableMapping` | alive; 6 pass, 1 fail, 21 error (19 s). It imports, but most of its own tests still do not pass. |
| `flask-restplus` (2019) | dead: needs a removed `werkzeug` function | still dead |

## Kiro Crew: the Undertaker

A Crew cron job is a natural-language prompt plus a schedule that Crew runs in its own session
([docs](https://kiro.dev/docs/crew/features/cron/)). That fits one job here: re-checking a watchlist of
abandoned packages every night and telling me which changed. The deterministic part is plain code
(`backend/necromancer/triage.py` and `undertaker/undertake.py`, 33 tests, mutation-checked), so the nightly
result is reproducible. The Crew job only runs the script and writes the briefing.

What is done: the script, the watchlist (14 packages whose latest release is older than 2021), the state file, the
report, the tests. I ran the script against PyPI twice: the first night reported all 14 as new, the second reported
no change.

What is not done: **I have not created or run the Crew job.** I could not run Crew where this was built, so the
command in `undertaker/README.md` is written from the documentation and is untested. Whether this qualifies for a
Crew prize depends on how the rules define a Crew app; I would not claim it until the job has actually run.

The static triage is also not a good predictor. Of the four development packages it called `flask-restplus`
revivable (it is still dead) and `hyper` "needs a person" (it came back with 95 tests passing). The report says so.

## The benchmark

I asked "how often does it actually work?" and ran it on lists of old packages it had not seen. The answer was worse
than the four showcase packages suggested, and it did not improve much when I worked on it: **1 of 21 dead packages came
back (5%) on the second list, and 1 of 20 (5%) on a third list that nothing was tuned on.** Every run, package and
caveat is in [`BENCHMARK.md`](BENCHMARK.md).

I did three rounds, in this order, and kept every result file:
1. First list: 3 of the 5 dead packages I had built the tool on came back, 0 of the 15 it had not seen. That gap is
   the finding: the tool was tuned to its own examples.
2. The benchmark exposed two bugs no test had (source files with a `# coding:` line silently dropped, so `xlwt` looked
   broken and was not; `install_requires` in `setup.py` never read). I fixed them and measured a second, fresh list.
3. I read that list's failures and made three changes: declare `setuptools` for code that imports `pkg_resources`
   or `distutils`, rewrite `inspect.getargspec` where the replacement provably means the same, and call a
   placeholder package "nothing to run" instead of dead. On the list they came from they revived 3 more packages. Then I
   measured a third list nobody had read, with and without the changes: **2 of 39 outcomes differed, one a revival.**

What I expected and what I got: I guessed the three changes might flip about 10 of 20 failures. They flipped 3 on the
list they were built from and 1 on a clean list. The cause I had named for the others was sometimes right and the
package then failed on something behind it (`fixture` on `pkg_resources.require` of itself, `sqlalchemy-migrate` on a
removed SQLAlchemy module). Python 2 code remains the largest cause of failure (8 of the 20), and the tool does not convert it.

One change nearly broke the public demo: revival now adds `setuptools<81` to `flask-restplus`, and the public host
refuses a dependency nobody reviewed. A check on the four demo packages caught it before anything shipped, and the
review script now reviews what a revival adds, not only what the package declares.

How I kept it honest: the package lists and the rule were written before each run; every candidate is recorded,
including the ones the rule excluded; the first run was kept untouched; I measured on a list I had not read; I ran the
clean list on the old and new code to see what the changes did; and I reported the rate that came out, not the one I
hoped for. Two detector refinements came from the second list and are included in the final run; they changed nothing on the third.

I also made one mistake during it, caught by the test suite: a variable I set for a local check
(`CRYPT_KEEPER_PROOF_OF_LIFE=off`) stayed in my shell and made 17 sandbox tests fail. The tests now clear it.

## Download and mutation checks for the new code

The export zip is checked three ways: the patch is tested to apply with `git apply` (including CRLF files, files
without a final newline, form feeds and non-ASCII text), a property test round-trips random edits through
`git apply`, and I downloaded a real export from the built image and applied it to attrdict's real original source.
I then broke 34 behaviours of the export and the Undertaker on purpose; 31 broke a test, and the 3 that did not were
equivalent mutants (the change had no observable effect), which I tightened or removed. The three later changes
were checked the same way: 26 breakages, 20 caught at once, and 6 survivors that showed real gaps. I closed four with new tests
(a `distutils` import in `setup.py` must stay a manual item, `[4]` must not be rewritten, `spec._asdict()` must not be
treated as a field, a class attribute is not a local), simplified one redundant check, and loosened one rule that was
stricter than it needed to be (any unpacking target is safe after `[:4]`, which I then verified by running the rewritten code).

## Limits and what I would do next

- Pin the transitive dependencies of the listed packages by hash.
- Run proof of life in a container with real isolation. The current sandbox is not a security boundary.
- Use the `necromancer` agent on a real change, and try the CLI, Web and Crew, none of which I have used.
- Convert Python 2 source. Today it is reported, not converted.
- Handle data files and C extensions, which the exhumer skips.
