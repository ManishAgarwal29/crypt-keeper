# Demo script (under 3 minutes)

Judges are not required to watch past three minutes, so the strongest moment comes first and the
video states the problem in one breath. Record the **live site**, since that is what judges can open:
`https://crypt-keeper.delightfulwater-add81b23.eastus2.azurecontainerapps.io/`. The numbers below come
from a rehearsal against it (Python 3.12, Azure Container Apps), so they are what the screen will show.

## Before recording

- [ ] **Wake the app.** It scales to zero, so the first request after a pause is slow. Open the URL and
      run `attrdict` once about a minute before recording; do not record that first run.
- [ ] The hint under the form must say **"Proof of life will run on Python 3.12.x"**. If it says "not
      installed", the verdicts will read unverified.
- [ ] Browser at 1920x1080, zoom 110%, one tab, notifications off, bookmarks and other tabs hidden.
- [ ] Rehearse the four headstones once in a row. Expect: attrdict 11 s, hyper 19 s, unittest2 19 s,
      flask-restplus 16 s. Run `python scripts/smoke_test.py <URL>` first; all checks must pass.
- [ ] Only the four headstones run proof of life on the public site. Do not type another package into
      the box for the main take; it gets an autopsy and diff but no verdict (that is the right behaviour,
      and worth one sentence if you show it).
- [ ] Do not demo `boto` (about 3 minutes) or `nose` (Python 2, no automatic fix).
- [ ] Optional second take on the local app (`cd backend; python -m uvicorn api.main:app --port 8000`)
      for any package you like, if you want to show it working beyond the four.

## Timeline

| Time | On screen | Say |
|---|---|---|
| 0:00 to 0:15 | The page, the four headstones | "Thousands of Python packages are abandoned. When Python moves on, they break, and people fork and suffer or rewrite. Crypt Keeper digs one up, finds why it died, fixes what it safely can, and then proves the result runs." |
| 0:15 to 0:55 | Click **attrdict**. The ritual stages tick. Verdict: **It lives!** | "attrdict was last released in 2019. On Python 3.12 it dies on one line: `from collections import Mapping`. Watch the ritual: exhume, autopsy, resurrect, then proof of life on the original and the revived package, in parallel." (Pause on the red **Dead** card with the exact error, then the green **Alive** card.) |
| 0:55 to 1:15 | Autopsy report, then the diff | "The autopsy names the cause. The fix is a precise text edit: comments and formatting stay. It moved the import to `collections.abc`." |
| 1:15 to 1:45 | Click **hyper** | "Same idea, bigger payoff. hyper, an HTTP/2 library from 2016. Before revival it cannot even be imported, so its own tests mostly error out. After: 95 of its tests pass. Some still fail, 13 of them. The revival fixes what broke on Python 3.12, not every bug in a ten-year-old library. The proof is the package's own test suite, not a claim." (Say the 13 out loud, or show the red count.) |
| 1:45 to 2:05 | Click **flask-restplus** | "It does not always work. flask-restplus needs a function that was removed from werkzeug. Crypt Keeper cannot fix that, and it says so: still dead. A tool that only reports wins is not worth trusting." |
| 2:05 to 2:40 | Cut to the repo: `.kiro/` folder | "Built with Kiro. A spec with 8 requirements and 13 tasks drove the order. Steering kept the Coroner and Necromancer pure, which is why there are 39 property-based tests on them. Three hooks: quick tests on save, the full suite after each task, and a safety review whenever the sandbox changes. An MCP server shares the exhumer with the app, and a read-only auditor agent reviewed the sandbox, found 12 problems, and all 12 are fixed and tested." |
| 2:40 to 2:55 | Terminal: test count, or the Kiro usage page | "About 700 tests. Real packages found the bugs tests missed: missing dependencies, a file cap that made boto look dead, a false pass on the wrong Python. Every one became a test." |
| 2:55 to 3:00 | Title card with the repo URL | "Crypt Keeper. Every dead package deserves a second life." |

If a run is slow on camera, start the next package's run while you narrate the previous one, or
speed up the waiting stretch in editing and keep the verdict at normal speed.

## Things not to say

- Do not call the sandbox "secure". It is not a security boundary; the README says so.
- Do not say it converts Python 2 code. It reports it.
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
