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

## Round 3: converting Python 2, capping removed names, declaring imports

The table above was the plan. Of the 20 packages still not alive on the third list, Python 2 code was 8, a dependency that
removed a name was 5, and an import the package never declares was 4. Each got a deterministic, tested lever:

1. **Declare imports the package forgot** (`necromancer/declare_imports.py`): a fixed import-name to distribution table. An
   import name that is not in the table is never guessed.
2. **Cap a dependency below the release that removed a name** (`necromancer/removed_names.py`): `Jinja2<3.1` for
   `evalcontextfilter` and `Markup`, `paramiko<4` for `DSSKey`, `Werkzeug<2.2` for `parse_rule`, and so on.
3. **Convert Python 2 source with `lib2to3`** (`necromancer/py2convert.py`), in memory. This is the one transform that is
   not a drop-in replacement, so it only runs on a package whose runtime code does not parse as Python 3, and it can be
   switched off with `CRYPT_KEEPER_CONVERT_PYTHON2=off`.

### The lists, in the order they were used

| List | What it was for | Previous commit (154ff82) | Final code |
|---|---|---|---|
| Third (39) | Where the levers came from. A development list. | 1 of 20 dead brought back (5%) | 6 of 20 (30%) |
| Fourth (42 of 64) | Written before any of this was run. The levers as first built brought back 5 of 16 against 4 for the previous commit. It then showed two flaws and which libraries are forgotten, so it became a development list. | 4 of 16 (25%) | 6 of 16 (38%) |
| **Fifth (42 of 63)** | **Written after the code was finished and not changed after it was run. The clean one.** | **3 of 14 (21%, 95% CI 8% to 48%)** | **5 of 14 (36%, 95% CI 16% to 61%)** |

On the clean list the final code brought back two more packages than the previous commit: `pyttsx` (Python 2 conversion) and
`soundcloud` (it declared `six`). `pycrypto` was already importable and its tests improved because its Python 2 test files
now run (0 passing, 45 errors became 31 passing, 73 failing, 8 errors). **The capping lever contributed nothing on either
clean list.** It only helped on the third list, which is where its table came from.

**What that does and does not say.** 3 of 14 against 5 of 14 is two packages. The two intervals overlap almost completely,
so this is not evidence of a real gain on its own. It points the same way as the fourth list (4 to 5, before the fixes) and
the development lists, and it came with no new regressions, but a different list could give a different rate. The one regression on
this list, `pickleshare` (importable before revival, not after), is identical on the previous commit, so it is not new.
The honest headline is "somewhere between a fifth and a half of the dead packages on lists like these", not 36%.

### What this round found wrong in its own work

- **The Python 2 trigger was too broad.** The first version converted a whole package when any file failed to parse, including a
  stray file in `tests/` or `examples/`. It rewrote 31 files of `bottle-cork`, which already worked, and turned a working
  package, `pdfrw`, into one that no longer imported. The trigger now needs a *runtime* file that does not parse
  (`pdfrw`'s two Python 2 files are an example and a test script, so it is left alone).
- **"Parses" was not a strong enough check.** `lib2to3` rewrote `def f(str=str, unicode=unicode)` as
  `def f(str=str, str=str)`, which parses and then fails to compile. A converted file must now compile.
- **One residual case.** `musicbrainzngs` still imports after revival and its own tests drop from 78 passing to 66 (the
  conversion rewrote 15 files). It has Python 2 syntax in a runtime file. The verdict says `already_alive` because it only
  compares whether the package imports, so a drop in passing tests is not flagged. A safe next step is to retry without
  conversion when the original imported and the revival does worse.
- **A declared import can be heavy.** `Cheetah` gained `Django` as a dependency because one optional integration file
  (`cheetah/Django.py`) imports it. The package came back, but it now installs a framework for an optional module.
- **`flask-restplus` moved one layer deeper.** It uses `from werkzeug import cached_property`, so it now gets
  `Werkzeug<1.0`. The old Flask line that needs then asks for `markupsafe.soft_unicode`, which no Python 3.12 wheel provides.
  It is still dead, for a different reason. On the public host, the new `Werkzeug` requirement is not in
  `backend/proof/approved_dependencies.json`, so that headstone now reads `unverified` until the dependency is reviewed
  (`python scripts/review_dependencies.py flask-restplus`, then `--write`). `attrdict`, `hyper` and `unittest2` are unchanged.
- **Three runs did not finish on the fourth list.** `zerorpc` and `gevent-websocket` timed out, and `gevent-socketio` hit a
  PyPI connection error. Their revival is identical with and without the levers (same files, same dependencies), and a rerun
  of all three finished in 14 to 27 seconds, so the rows from that rerun replace the failed ones. A first merge also wrote a
  byte-order mark into the results file; it was removed and the file reads as before.

### How the tables were checked

Every entry in `removed_names.py` was installed from wheels only on Python 3.12.10 (the sandbox's rule) in a fresh virtual
environment, and the removed name was imported. Each also has a control showing the name is gone above the ceiling:

| Cap | Resolves to | Names that import | Control |
|---|---|---|---|
| `Jinja2<3.1` | 3.0.3 | `evalcontextfilter`, `contextfilter`, `environmentfilter`, the `*function` variants, `Markup`, `escape` | 3.1.6 lacks `evalcontextfilter` |
| `itsdangerous<2.1` | 2.0.1 | `TimedJSONWebSignatureSerializer`, `JSONWebSignatureSerializer`, `itsdangerous.json` | 2.2.0 lacks it |
| `paramiko<4` | 3.5.1 | `DSSKey` | 5.1.0 lacks it |
| `Werkzeug<2.2` | 2.1.2 | `routing.parse_rule` | 2.2.3 lacks it |
| `Werkzeug<2.1` | 2.0.3 | `security.safe_str_cmp` | (conservative: only the safe side was tested) |
| `Werkzeug<1.0` | 0.16.1 | `werkzeug.contrib`, `cached_property`, `url_quote`, `url_encode` | 1.0.1 lacks `cached_property` |
| `Werkzeug<3` | 2.3.8 | `urls.url_quote` | 3.1.9 lacks it |
| `Flask<1.0` with `Werkzeug<1.0`, `Jinja2<3.1`, `itsdangerous<2.1` | flask 0.12.5 | `import flask.ext` | |
| `Flask<3` with `Werkzeug<3` | flask 2.3.3 | `Markup`, `escape`, `_request_ctx_stack`, `_app_ctx_stack` | Flask 3 lacks `_request_ctx_stack` |
| `Flask<2.3` with `Werkzeug<2.3` | flask 2.2.5 | `json.JSONEncoder` | |

`MarkupSafe<2.1` was left out on purpose: it has no Python 3.12 wheels, so a cap that needs it cannot install. Every
distribution in `declare_imports.py`'s table was installed from wheels on 3.12 and its import name imported (gevent, Twisted,
eventlet, greenlet, pyzmq, msgpack, ujson, Markdown, Pygments, Babel, SQLAlchemy, Django, redis, pika, PyJWT, pyOpenSSL,
cryptography, boto3, psutil, tqdm, colorama, tabulate, Unidecode, termcolor, xlrd, openpyxl, cffi). `unittest._TextTestResult`
is absent and `unittest.TextTestResult` present on 3.12.10, and both exist on 3.11.6.

### What is still dead on the clean list

Eight packages stayed `still_dead` with the final code and four are `unverified`. The reasons are in the per-package table.
They are not new kinds of failure: a C extension or a dependency with no wheel (`py-bcrypt`, `simple-crypt`,
`goose-extractor`), the removed `imp` module (`nose-progressive`), a name still removed (`shove`), an import that is not in
the table (`tenjin`), and packaging tools that cannot be imported without `distutils` (`distribute`).

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
  response. Round 3 then did change the tool from those failures, so the third and fourth lists became development
  lists, and the fifth list is the clean one. A sixth list would be the next clean measurement.
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
with the three changes, before the two refinements) and `results-run4-third-final.jsonl` (third list, the code at commit
154ff82: the previous headline).

Round 3, all on Python 3.12 in a fresh container, each pair run against the same PyPI on the same day:
`results-run5-fourth-before.jsonl` and `results-run5-fourth-after.jsonl` (fourth list, commit 154ff82 and the levers as first
built), `results-run6-third-new-levers.jsonl` (third list, levers as first built), `results-run7-fifth-before.jsonl` and
`results-run7-fifth-after.jsonl` (the clean fifth list, commit 154ff82 and the final code: **the headline**), and
`results-run8-third-final.jsonl` and `results-run8-fourth-final.jsonl` (development lists, final code). To rebuild the
baseline image: `git worktree add --detach ../ck-baseline 154ff82`, then `docker build -t crypt-keeper:base .` there.
