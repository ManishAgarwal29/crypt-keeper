## Why the 20 packages in the final list are not alive (my reading, by hand)

I read the error of every package in the third list that was not importable after revival, plus the placeholder.
The script's own cause counts above are coarse; this is a closer reading, and the grouping is a judgement, not a
measurement.

| Cause | Packages | Count |
|---|---|---|
| Python 2 code: syntax (`except A, e:`, assigning to `True`), or implicit relative imports (`import Workbook` meaning a sibling module). Crypt Keeper reports this and does not convert it. | webapp2, mongokit, pycassa, pydns, pyexcelerator, ClientForm, requests-oauth, couchdbkit | 8 |
| A newer release of a dependency removed what the package uses (`jinja2.evalcontextfilter`, `flask.ext`, `werkzeug.routing.parse_rule`, `pymongo.Connection`, `paramiko.DSSKey`). | Flask-Triangle, Flask-Autodoc, Flask-Classy, minimongo, pysftp | 5 |
| The package imports something it never declares (`nose`, `bottle`, `six`), so nothing installs it. | nose-testconfig, nose-ignore-docstring, bottle-sqlite, ipcalc | 4 |
| A declared dependency could not be installed from a wheel. | Pylons, Flask-MongoAlchemy | 2 |
| No Python source of its own: a placeholder that installs another package. Reported `unverified`, in no rate. | dnspython3 | 1 |

Python 2 code is again the largest cause (8 of 20). The next lever would be converting it, which is a bigger job than
this tool takes on; Python 3.12 still ships `lib2to3`, but it is gone in 3.13.

## What changed between the lists, and what it did

Three changes were made after reading the second list's failures (`benchmark/packages-fresh.txt`), then measured on a
list nobody had read (`benchmark/packages-third.txt`):

1. **Declare `setuptools`** when runtime code imports `pkg_resources`, `distutils` or `setuptools` (`setuptools<81` for
   `pkg_resources`, because the newest setuptools has none). Checked on 3.12.10: a fresh virtualenv has no setuptools,
   `distutils` works once it is installed, and `pkg_resources` is missing from release 84.2.1 but present in 80.10.2.
2. **Rewrite `inspect.getargspec`** only where the replacement means the same: unpacking into four names
   (`getfullargspec(f)[:4]`), field access (`.keywords` becomes `.varkw`), and indexes 0 to 3. Anything else stays with a
   person, because `getfullargspec` returns seven fields, not four.
3. **Report a placeholder as "nothing to run"**, not as dead: a distribution that is only a `setup.py` has nothing to import.

Two refinements came from the same reading: an import in an `except ImportError:` handler counts (it is what runs when
the optional import fails), and `__import__('pkg_resources')` counts. Neither changed any result in the third list.

| Where | Result |
|---|---|
| The list the changes were built from (tuned on) | 3 more packages came back (`baker`, `fn`, `begins`), 2 placeholders were reclassified, 1 package improved (`Whoosh`). Several I had expected to flip did not: `fixture` then failed on `pkg_resources.require` of its own distribution, `sqlalchemy-migrate` on a removed SQLAlchemy module. |
| The third list, with and without the changes | **Two of 39 outcomes differ.** `pyfits` went from dead to alive, and `dnspython3` from "dead" to "nothing to run". |

So on packages I had not read, the three changes were worth one revival in 39. I expected more.

## What the benchmark found in Crypt Keeper itself

- **Two bugs, fixed in an earlier round.** Source files with a `# coding:` line were dropped, which made `xlwt` look
  broken when it was fine, and `install_requires` in `setup.py` was never read.
- **One regression** (second list, before the changes): `pyelasticsearch` imported before revival and not after. The
  revised requirements allowed a newer `elasticsearch` that no longer has a module it needs. Loosening a cap can break a
  package that worked. The verdict shows this as `regressed`, which is what it is for, but it is a real weakness.
- **Declaring a dependency can break the public demo.** Revival added `setuptools<81` to `flask-restplus`, and the public
  host refuses dependencies nobody reviewed. Caught by a check on the four demo packages, fixed by reviewing it
  (`scripts/review_dependencies.py` now reviews what a revival adds, not only what the package declares).
- **The tool is far less effective on packages it was not built around.** On the 5 dead packages used while building it,
  3 came back. On the first 20 it had never seen, 1 did, and on the next 20, 1.

## What this does not tell you

- The packages are lists I wrote by a fixed rule (`benchmark/packages*.txt`). They are old packages in families I could
  think of, not a random sample of abandoned PyPI packages. A different list would give a different rate.
- Excluded candidates are listed. Many "abandoned" packages I expected to be old had releases after 2021.
- "Brought back" means it imports on Python 3.12 in a fresh virtualenv with wheel-only installs. It does not mean the
  package works: see the test columns, where many revived packages still fail tests.
- Each package ran once, on one day, against whatever PyPI served that day.
- Around 40 packages per list is a small sample. The confidence interval is wide: for this kind of list the true rate
  could be anywhere from about 1% to 24%.
- I read the failures of the third list after running it, to write the table above, and did not change the tool in
  response. A fourth list would be the next clean measurement.
- Running these packages' code happened in a throwaway container. That container is not a security boundary either.

## How to reproduce

```powershell
docker build -t crypt-keeper .
docker run --rm -p 8080:8080 -e PORT=8080 -e CRYPT_KEEPER_PUBLIC=off -e "CRYPT_KEEPER_PROOF_ALLOWLIST=*" crypt-keeper
python scripts/benchmark.py run http://127.0.0.1:8080 --packages benchmark/packages-third.txt --out benchmark/results-mine.jsonl
python scripts/benchmark.py report benchmark/results-mine.jsonl
```

Result files, in the order they were produced: `results-run1-before-fixes.jsonl` (first list, before the two bug fixes),
`results-run2-after-fixes.jsonl` (the same list after), `results-run2-fresh-before-changes.jsonl` (second list),
`results-run3-fresh-after-changes.jsonl` (second list after the three changes),
`results-run3-third-without-changes.jsonl` (third list on the image without them), `results-run3-third.jsonl` (third list
with the three changes, before the two refinements) and `results-run4-third-final.jsonl` (third list, final code: the headline).
