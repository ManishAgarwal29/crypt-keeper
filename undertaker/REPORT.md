# Undertaker report, 2026-11-25

Watched 14 abandoned packages against Python 3.12. This is a static triage: nothing was run.
A package marked `revivable` has had every removed API rewritten, which is a prediction, not proof.
Confirm it with proof of life (`python scripts/smoke_test.py <server> --package NAME` or the web page).

## Needs your attention

- Nothing changed that needs a decision.

## Every package

| Package | Status | Last release | Code findings | Files rewritten | Left for a person | Blockers |
|---|---|---|---|---|---|---|
| BeautifulSoup | needs_human | 2019-10-05 | 3 | 0 | 3 | - |
| Flask-Cache | needs_human | 2014-04-21 | 2 | 0 | 2 | - |
| Flask-Script | needs_human | 2017-09-06 | 6 | 0 | 6 | - |
| args | alive | 2012-05-08 | 0 | 0 | 0 | - |
| attrdict | revivable | 2019-02-01 | 6 | 4 | 0 | - |
| flask-restplus | revivable | 2019-08-12 | 2 | 2 | 0 | - |
| fudge | needs_human | 2017-12-12 | 3 | 0 | 3 | - |
| hyper | needs_human | 2016-09-27 | 8 | 2 | 4 | - |
| mox | needs_human | 2010-08-15 | 3 | 0 | 3 | - |
| pep8 | needs_human | 2017-10-24 | 11 | 0 | 11 | - |
| python-gflags | needs_human | 2017-10-27 | 2 | 0 | 2 | - |
| suds-jurko | needs_human | 2014-01-24 | 29 | 0 | 29 | - |
| unittest2 | revivable | 2015-06-30 | 1 | 1 | 0 | - |
| xlwt | alive | 2017-08-22 | 0 | 0 | 0 | - |
