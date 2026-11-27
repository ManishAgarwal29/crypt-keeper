# Demo script (under 3 minutes)

Judges are not required to watch past three minutes, so the strongest moment comes first and the
video states the problem in one breath. Record the **live site**, since that is what judges can open:
`https://crypt-keeper.delightfulwater-add81b23.eastus2.azurecontainerapps.io/`. The numbers below come
from a rehearsal against it (Python 3.12, Azure Container Apps), so they are what the screen will show.

## Before recording

- [ ] **Confirm the live URL still exists.** On 2026-11-26 the host above did not resolve in DNS (GitHub and PyPI
      did), so the Azure app may have been stopped or deleted. Open it, run `python scripts/smoke_test.py <URL>`,
      and redeploy with `docs/DEPLOY.md` if it is gone. Do not record until it answers.
- [ ] **Wake the app.** It scales to zero, so the first request after a pause is slow. Open the URL and
      run `attrdict` once about a minute before recording; do not record that first run.
- [ ] The hint under the form must say **"Proof of life will run on Python 3.12.x"**. If it says "not
      installed", the verdicts will read unverified.
- [ ] Browser at 1920x1080, zoom 110%, one tab, notifications off, bookmarks and other tabs hidden.
- [ ] Rehearse the four headstones once in a row. Expect: attrdict 11 s, hyper 19 s, unittest2 19 s,
      flask-restplus 20 s. Run `python scripts/smoke_test.py <URL>` first; all checks must pass for the first three.
      For `flask-restplus` the "verdict is resurrected" check fails on purpose: it is the still-dead example.
- [ ] Only the four headstones run proof of life on the public site. Do not type another package into
      the box for the main take; it gets an autopsy and diff but no verdict (that is the right behaviour,
      and worth one sentence if you show it).
- [ ] Do not demo `boto` (about 3 minutes) or `nose` (Python 2, no automatic fix).
- [ ] Optional second take on the local app (`cd backend; python -m uvicorn api.main:app --port 8000`)
      for any package you like, if you want to show it working beyond the four.

## Timeline

| Time | On screen | Say |
|---|---|---|
| 0:00 to 0:15 | The page, the four headstones | "Thousands of Python packages are abandoned. When Python moves on, they break, and people fork and suffer or rewrite. Crypt Keeper digs one up, finds why it died, fixes what it safely can, and then runs the result to prove what came back." |
| 0:15 to 0:55 | Click **attrdict**. The ritual stages tick. Verdict: **It lives!** | "attrdict was last released in 2019. On Python 3.12 it dies on one line: `from collections import Mapping`. Watch the ritual: exhume, autopsy, resurrect, then proof of life on the original and the revived package, in parallel." (Pause on the red **Dead** card with the exact error, then the green **Alive** card.) |
| 0:55 to 1:15 | Autopsy report, then the diff. Click **Download the revival (.zip)** | "The autopsy names the cause. The fix is a precise text edit: comments and formatting stay. It moved the import to `collections.abc`. And you can take it with you: one click gives a patch you apply with git, the revived files and a report." (Show the zip's file list, or run `git apply` on it, if you have the seconds.) |
| 1:15 to 1:45 | Click **hyper** | "Same idea, bigger payoff. hyper, an HTTP/2 library from 2016. Before revival it cannot even be imported, so its own tests mostly error out. After: 95 of its tests pass. Some still fail, 13 of them. The revival fixes what broke on Python 3.12, not every bug in a ten-year-old library. The proof is the package's own test suite, not a claim." (Say the 13 out loud, or show the red count.) |
| 1:45 to 2:05 | Click **flask-restplus** | "It does not always work. flask-restplus needs a function that newer werkzeug removed. Crypt Keeper caps werkzeug below that release, but the old Flask that pulls in needs a MarkupSafe too old to install on Python 3.12. It says so: still dead, and it shows what to try next. A tool that only reports wins is not worth trusting." |
| 2:05 to 2:40 | Cut to the repo: `.kiro/` folder | "Built with Kiro. A spec with 8 requirements and 13 tasks drove the order. Steering kept the Coroner and Necromancer pure, which is why there are 39 property-based tests on them. Three hooks: quick tests on save, the full suite after each task, and a safety review whenever the sandbox changes. An MCP server shares the exhumer with the app, and a read-only auditor agent reviewed the sandbox, found 12 problems, and all 12 are fixed and tested." |
| 2:40 to 2:55 | `docs/BENCHMARK.md`: the headline numbers | "About 950 tests. And I measured it honestly: on a fresh list of old packages nothing was tuned on, 14 were dead on Python 3.12. The previous version brought back 3, the final one 5. That is a small sample and the intervals overlap, and I say so in the repo. Its autopsy named the cause for 10 of 19 on an earlier list, all of the Python 2 ones, and missed the rest. The benchmark also found bugs in the tool, and I fixed them and measured again." |
| 2:55 to 3:00 | Title card with the repo URL | "Crypt Keeper. It tells you why it died, fixes what it can, and shows you the proof." |

If a run is slow on camera, start the next package's run while you narrate the previous one, or
speed up the waiting stretch in editing and keep the verdict at normal speed.

## Things not to say

- Do not say Crypt Keeper "revives X% of abandoned packages" without the denominator. The honest line is 5 of 14
  dead packages in a list nothing was tuned on (36%, interval 16% to 61%; the previous version got 3 of 14; see `docs/BENCHMARK.md`). The four headstones are the ones it was built on.
- Do not say it was built with, or runs on, Kiro Crew. The Undertaker script exists and is tested; the Crew job has
  not been created or run.

- Do not call the sandbox "secure". It is not a security boundary; the README says so.
- Do not say it reliably converts Python 2 code. It converts syntax with lib2to3 and the tests may still fail.
- Do not say the autopsy predicts every cause. It names Python 2 code and removed standard-library names. It does
  not name names a third-party dependency removed, or imports a package forgot to declare (see `docs/DIAGNOSIS.md`).
  The proof-of-life error is what shows those.
- Do not say the verdict proves the package works everywhere. It proves it imports and passes the
  tests that ran, on the stated Python.

## Submission checklist (from the Devpost rules)

- [ ] Category: **Frankenstein** or **Resurrection** (decide before recording; the opening line should
      match it: "stitched together from four technologies" or "brings dead packages back to life").
- [ ] Public GitHub repo with an open-source license detected (MIT is in place).
- [ ] `/.kiro` is committed and not in `.gitignore` (it is not).
- [ ] **URL to the working app.** Deploy with `docs/DEPLOY.md`, then run
      `python scripts/smoke_test.py <URL>`. The public build runs proof of life only for the four
      headstones; any other package gets the autopsy and revival without a verdict. Put the URL
      and a one-line note about that in the Devpost form, and say judges can also run it locally.
- [ ] Text description of features and functionality (the README is a good base).
- [ ] Demo video under 3 minutes, public on YouTube, no copyrighted music.
- [ ] Kiro usage write-up (`docs/KIRO_USAGE.md`). Check its caveats are still true before submitting.
- [ ] Bonus: social post starting with `#KiroweenHackathon26` during the submission period, and a
      public blog post that says it was written for the Kiroween 2026 hackathon. Add both URLs.
- [ ] Request the 4,000 free Kiro credits by Dec 1, 12:00 pm PT. Deadline: Dec 4, 2:00 pm PT.
- [ ] `Overview.md` in the repo root is Devpost's text, not part of the project. Consider removing it.
