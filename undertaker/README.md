# The Undertaker

A nightly check on a watchlist of abandoned Python packages. It answers one question: **did anything change
that makes a dead package worth reviving?**

```powershell
python undertaker/undertake.py          # from the repository root; needs the backend requirements installed
```

For each package in `watchlist.txt` it fetches the latest release from PyPI, runs the Coroner (what is
broken on Python 3.12) and the Necromancer (what can be rewritten), triages the result, and compares it with
the previous night (`state.json`). It writes `REPORT.md` and lists what needs attention:

| Change | Meaning |
|---|---|
| `newly_revivable` | Every removed API in it can now be rewritten. A rule was added, or a dependency moved. |
| `new_release` | The package published a new release. Someone may have revived it upstream. |
| `no_longer_revivable` | It was revivable and is not anymore. |
| `unreadable` / `readable_again` | PyPI could not be reached for it, and the last known status is kept meanwhile. |

It is **static and deterministic**: it executes no package code and uses no LLM, so the same night gives the same report.

## How good is the prediction?

Not very, and the report says so. The status is a prediction from the rewrites alone. Checked against the real
proof-of-life runs on Python 3.12 (see `docs/BENCHMARK.md` for the full set):

- `flask-restplus` is triaged `revivable` but is **still dead**: it needs a function removed from `werkzeug`,
  which no rewrite can fix.
- `hyper` is triaged `needs_human` but **comes back to life** with 95 of its tests passing: the leftover items
  do not stop it importing.
- `pep8` is triaged `needs_human` and is in fact alive on 3.12.

So treat `revivable` and `new_release` as "worth checking", and confirm with proof of life:

```powershell
python scripts/smoke_test.py http://127.0.0.1:8000 --package NAME     # against a local server
```

## Running it nightly with Kiro Crew

> **Not tested.** I wrote this from the Crew documentation ([Scheduling](https://kiro.dev/docs/crew/features/cron/))
> and could not run Crew where this was built. The script itself is tested (`backend/tests/test_undertaker.py`);
> the Crew job below is what you create, and you will see in Crew's Schedule page whether it works.

A Crew cron job is a natural-language prompt plus a schedule, created in Crew itself. From a terminal where the
Crew CLI is installed (replace the path and the timezone):

```powershell
kirocrew cron add "undertaker" "In the Crypt Keeper repository at C:\path\to\crypt-keeper, run: python undertaker/undertake.py . Read undertaker/REPORT.md and tell me, in a few lines, which packages need attention and why. For each package that is newly revivable or has a new release, say what to check next: run python scripts/smoke_test.py against a local Crypt Keeper server with --package NAME. The statuses are static predictions, not proof, and say so. Change no files except undertaker/state.json and undertaker/REPORT.md. Do not commit or push." --cron "0 3 * * *" --timezone "America/Los_Angeles" --no-persistent-session --timeout-secs 1800
```

You can also ask Crew in chat to create the same job, or add it in the dashboard's Schedule panel. Then use
`kirocrew cron trigger <job_id>` to run it once and read the run's log on the Schedule page.

Things to know, from the Crew docs:
- Jobs fire only while the Crew gateway is running. A run missed while it was down is not replayed.
- The docs say recurring runs never get "auto-approve this session" trust. I have not seen how that plays out for
  this job, so check the first run's log for tool calls that needed approval, and allow only what the job needs
  (running the script, reading the report).
- Each run's result goes to the dashboard notifications (and Slack, if configured).
- If you want the job to be fully deterministic and need no model, Crew also supports script jobs (`--script`),
  which must live under `~/.kiro/crew/crons/`. The agent job above is what lets it write a short briefing.

## Files

| File | What it is |
|---|---|
| `undertake.py` | The script. Orchestration only: the logic is in `backend/necromancer/triage.py` (pure, tested). |
| `watchlist.txt` | The packages to watch. Add a line to watch another. |
| `state.json` | Last night's triage, used to see what changed. Committed so the history is visible in git. |
| `REPORT.md` | The latest report. |
