# Demo script (under 3 minutes)

Judges are not required to watch past three minutes, so the strongest moment comes first and the
video states the problem in one breath. Timings below come from real runs on Python 3.12.10.

## Before recording

- [ ] `py -0p` lists 3.12. Start the app: `cd backend; python -m uvicorn api.main:app --port 8000`.
- [ ] Open `http://127.0.0.1:8000`. The hint under the form must say **"Proof of life will run on
      Python 3.12.x"**. If it says "not installed", the verdicts will read unverified.
- [ ] Browser at 1920x1080, zoom 110%, one tab, notifications off, nothing else heavy running
      (the sandbox builds virtualenvs).
- [ ] Do a dry run of all three packages first, so PyPI and pip caches are warm.
- [ ] Do not demo `boto` (about 3 minutes) or `nose` (Python 2, no automatic fix).

## Timeline

| Time | On screen | Say |
|---|---|---|
| 0:00 to 0:15 | The page, the four headstones | "Thousands of Python packages are abandoned. When Python moves on, they break, and people fork and suffer or rewrite. Crypt Keeper digs one up, finds why it died, fixes what it safely can, and then proves the result runs." |
| 0:15 to 0:55 | Click **attrdict**. The ritual stages tick. Verdict: **It lives!** | "attrdict was last released in 2019. On Python 3.12 it dies on one line: `from collections import Mapping`. Watch the ritual: exhume, autopsy, resurrect, then proof of life on the original and the revived package, in parallel." (Pause on the red **Dead** card with the exact error, then the green **Alive** card.) |
| 0:55 to 1:15 | Autopsy report, then the diff | "The autopsy names the cause. The fix is a precise text edit: comments and formatting stay. It moved the import to `collections.abc`." |
| 1:15 to 1:45 | Click **hyper** | "Same idea, bigger payoff. hyper, an HTTP/2 library from 2016. Before: 4 tests pass and 13 error. After revival: 95 tests pass. The proof is the package's own test suite, not a claim." |
| 1:45 to 2:05 | Click **flask-restplus** | "It does not always work. flask-restplus needs a function that was removed from werkzeug. Crypt Keeper cannot fix that, and it says so: still dead. A tool that only reports wins is not worth trusting." |
| 2:05 to 2:40 | Cut to the repo: `.kiro/` folder | "Built with Kiro. A spec with 8 requirements and 13 tasks drove the order. Steering kept the Coroner and Necromancer pure, which is why there are 31 property-based tests on them. Three hooks: quick tests on save, the full suite after each task, and a safety review whenever the sandbox changes. An MCP server shares the exhumer with the app." |
| 2:40 to 2:55 | Terminal: test count, or the Kiro usage page | "Real packages found the bugs tests missed: missing dependencies, a file cap that made boto look dead, a false pass on the wrong Python. Every one became a test." |
| 2:55 to 3:00 | Title card with the repo URL | "Crypt Keeper. Every dead package deserves a second life." |

If a run is slow on camera, start the next package's run while you narrate the previous one, or
speed up the waiting stretch in editing and keep the verdict at normal speed.

## Things not to say

- Do not call the sandbox "secure". It is not a security boundary; the README says so.
- Do not say it converts Python 2 code. It reports it.
- Do not say the verdict proves the package works everywhere. It proves it imports and passes the
  tests that ran, on the stated Python.

## Submission checklist (from the Devpost rules)

- [ ] Category: **Frankenstein**.
- [ ] Public GitHub repo with an open-source license detected (MIT is in place).
- [ ] `/.kiro` is committed and not in `.gitignore` (it is not).
- [ ] **URL to the working app.** Open decision: the app executes package code, so a public host
      needs a container around the sandbox, or `CRYPT_KEEPER_PROOF_OF_LIFE=off` (which keeps
      exhume, autopsy, revival and diffs, and drops the verdict). Judges can also run it locally
      from the README.
- [ ] Text description of features and functionality (the README is a good base).
- [ ] Demo video under 3 minutes, public on YouTube, no copyrighted music.
- [ ] Kiro usage write-up (`docs/KIRO_USAGE.md`). Check its caveats are still true before submitting.
- [ ] Bonus: social post starting with `#KiroweenHackathon26` during the submission period, and a
      public blog post that says it was written for the Kiroween 2026 hackathon. Add both URLs.
- [ ] Request the 4,000 free Kiro credits by Dec 1, 12:00 pm PT. Deadline: Dec 4, 2:00 pm PT.
- [ ] `Overview.md` in the repo root is Devpost's text, not part of the project. Consider removing it.
