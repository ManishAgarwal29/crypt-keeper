# Deploying Crypt Keeper

This puts the app on a public URL, for example for the Kiroween submission. Read "Safety model"
first: this app can run code, and the settings below decide whose.

## Safety model

Proof of life runs a package's own code. The container is not a security boundary against that
code, so the public image only runs packages from a **curated list**:

- `CRYPT_KEEPER_PROOF_ALLOWLIST` (set in the `Dockerfile` to `attrdict,hyper,unittest2,flask-restplus`)
  names the only packages the server will execute.
- Any other package still gets the live exhume, autopsy, revival and diffs. None of that executes
  anything. The verdict reads `unverified` and says why.
- `/proof-of-life`, which runs source the caller posts, returns 403 while any list is in force.

**Every ambiguous setting fails closed.** The image sets `CRYPT_KEEPER_PUBLIC=1`. With it, a *missing*
allow-list means no package runs, and a blank one means none in any mode. So deleting the variable by
mistake, or a quoting slip that leaves it empty, switches execution off instead of on. Running every
package takes an explicit `CRYPT_KEEPER_PROOF_ALLOWLIST=*`, which this image never sets. Do not set it
on a public service. Any unrecognised value of `CRYPT_KEEPER_PUBLIC` also counts as public.

Do not add a package to the list unless you have looked at it, and its dependencies. The code that runs
is the latest PyPI release of each listed package and of everything it depends on.

**Dependencies need a review too.** On a public host, a listed package only runs if every direct
dependency it declares is named in `backend/proof/approved_dependencies.json` for that package. A
package with no entry in the file does not run. To add a package:

```powershell
python scripts/review_dependencies.py somepackage            # prints its direct dependencies and the full set pip would install
# read them, then record what you read:
python scripts/review_dependencies.py somepackage --write
```

The review covers what the package declares **and what a revival adds** (for example `setuptools<81` when the code imports
`pkg_resources`; `flask-restplus` needed this), because the server runs the revived package too and refuses a dependency
nobody reviewed. Then add the name to `CRYPT_KEEPER_PROOF_ALLOWLIST` and rebuild the image. A test fails if a package
on the `Dockerfile` list has no entry. This covers **direct** dependencies only: pip still picks the
latest release of their dependencies at run time, so a malicious new release of one of those would be
installed. A review of the four listed packages found 15 projects in the full graph, all on PyPI today.
Hash-pinned lock files would close that and are not done.

Other things to know:

- **Use a service account with no permissions** (the steps below). Code in the container can ask the
  cloud metadata server for the runtime service account's token. By default Cloud Run uses the Compute
  Engine default account, which in many projects holds broad roles. With a dedicated account that has
  no roles, a stolen token can do almost nothing.
- **No secrets in the service environment.** On Linux a child process running as the same user can
  read its parent's environment through `/proc`. The sandbox scrubs the child's own environment,
  not that. Do not set tokens on this service.
- The container has outbound internet access (the sandbox needs it for `pip`).
- Turn execution off completely with `CRYPT_KEEPER_PROOF_OF_LIFE=off`.
- **Hostile uploads are bounded.** Request bodies are limited by the bytes actually received (a chunked
  request has no `Content-Length`), and an archive is refused before it is unpacked if it has more than
  20,000 entries or claims more than 250 MB, on top of the 20 MB download cap.
- **The sandboxed child is limited.** Output over 10 MB, a run directory over 1 GB and a total run time
  over 600 s stop the run. On Linux the child also has limits on core dumps, file size (256 MB),
  process count and address space (3 GB of virtual memory, not resident memory;
  `CRYPT_KEEPER_CHILD_MEM_MB`, `0` turns it off). A large package may need the limit raised. Processes
  a package leaves behind are killed after each step.
- **Not done:** a per-IP rate limit (cost and load are bounded by `--max-instances`, the slot limit
  `CRYPT_KEEPER_MAX_PROOFS` and the exhume limit `CRYPT_KEEPER_MAX_EXHUMES`), and running the child as a
  different user from the app. Acceptable for a short demo and worth doing before a real launch.

## What was and was not tested

Tested here, on the real image (rebuilt from this `Dockerfile` after the safety fixes, Python 3.12 inside):

- the image builds and starts, and the UI loads from it
- `scripts/smoke_test.py` passes all its checks against it: health, Python 3.12 present, a full
  streamed `attrdict` run (`resurrected`, 8 to 16 s in my runs), progress events arriving one by one, an
  unlisted package (`docopt`) getting an autopsy but **no execution**, and a posted-source request
  refused with 403
- **the deploy mistake the fail-closed rule is for:** a container started with a *blank*
  `CRYPT_KEEPER_PROOF_ALLOWLIST`. Nothing executed, the verdict said why, and posted source got 403.
  The same result with the variable removed entirely, on a local server with `CRYPT_KEEPER_PUBLIC=1`
- a real `uvicorn` server over a socket: a chunked body over the 25 MB limit gets 413 (and a 1 MB one
  gets through to normal validation), a declared 100 MB body gets 413, and malformed or conflicting
  `Content-Length` headers get 400. The server survived all of it
- the archive caps against real packages. The largest source archive I tried (`django`) has 10,312
  entries and 47 MB unpacked, `sqlalchemy` has 69 MB unpacked; the caps are 20,000 entries and 250 MB
- the proxy and host rules, using the headers a platform sends: https origin with
  `X-Forwarded-Proto: https` is accepted; a missing header, an http page against an https site, a
  foreign origin and an unknown Host are refused; a `*.run.app` host is accepted
- the UI against the container, including the curated-list notice and the skipped stages

**Not tested** (no `gcloud` or `flyctl` here, and no cloud account):

- the deploy commands below, including the service-account steps, which are written from the Cloud
  Run documentation
- how Cloud Run's own front end treats very large or chunked requests (our limit is 25 MB)
- streaming through Cloud Run's front end. It is reported to work for chunked responses without
  special settings, but I have not seen it. If it buffers, the UI still works, but the progress
  list jumps to done at the end.
- cold-start time and memory sizing under real traffic

Run `scripts/smoke_test.py` against the deployed URL first. It tells you which of these held.

## Cloud Run

You need a Google account, a project with billing enabled, and the
[gcloud CLI](https://cloud.google.com/sdk/docs/install). New customers get free credit, and Cloud Run
scales to zero when idle.

**1. Create a runtime service account with no roles.** Do not skip this: it is what limits the damage
if code in the container asks the metadata server for credentials.

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

gcloud iam service-accounts create crypt-keeper-run `
  --display-name "Crypt Keeper runtime (no roles)"
```

Do not grant this account any role. If a later step fails with `iam.serviceaccounts.actAs`, grant
*yourself* the Service Account User role on this one service account. That is a permission for you to
attach it, not a permission for the account.

**2. Deploy with it.**

```powershell
# from the repository root (where the Dockerfile is)
gcloud run deploy crypt-keeper `
  --source . `
  --region us-central1 `
  --service-account crypt-keeper-run@YOUR_PROJECT_ID.iam.gserviceaccount.com `
  --allow-unauthenticated `
  --cpu 2 --memory 2Gi `
  --timeout 900 `
  --concurrency 4 `
  --max-instances 3
```

The first run asks to enable the Cloud Build, Artifact Registry and Cloud Run APIs and to create a
repository. Answer yes. It builds the image remotely, so you do not need Docker. When it finishes it
prints the **Service URL**.

**3. Check the account and the roles.** Both commands should show what is written here:

```powershell
# prints crypt-keeper-run@YOUR_PROJECT_ID.iam.gserviceaccount.com
gcloud run services describe crypt-keeper --region us-central1 `
  --format "value(spec.template.spec.serviceAccountName)"

# prints nothing: the account holds no roles on the project
gcloud projects get-iam-policy YOUR_PROJECT_ID --flatten "bindings[].members" `
  --filter "bindings.members:crypt-keeper-run@" --format "table(bindings.role)"
```

**4. Check the app:**

```powershell
python scripts/smoke_test.py https://YOUR-SERVICE-URL
```

What the flags do:

| Flag | Why |
|---|---|
| `--cpu 2 --memory 2Gi` | Each proof of life builds a virtualenv and installs packages; a run uses two at once. |
| `--timeout 900` | A run takes 10 to 60 seconds for the listed packages; the default of 5 minutes is enough, this leaves room. |
| `--concurrency 4` | Requests handled by one instance at once. Lower it if runs fail from memory pressure. |
| `--max-instances 3` | Caps cost and load if the URL is shared widely. |
| `--allow-unauthenticated` | Judges must be able to open the URL without logging in. |

For the judging period you can add `--min-instances 1` to avoid the first-request cold start. It
bills continuously, so check the price and remove it afterwards.

### Settings (environment variables)

| Variable | Default in the image | Meaning |
|---|---|---|
| `CRYPT_KEEPER_PUBLIC` | `1` | Public-host mode: a missing allow-list means *no* package runs. Leave it on. |
| `CRYPT_KEEPER_PROOF_ALLOWLIST` | `attrdict,hyper,unittest2,flask-restplus` | Packages that may be executed. Blank means none. `*` alone means every package (never on a public host). |
| `CRYPT_KEEPER_PROOF_OF_LIFE` | on | `off` disables execution entirely. |
| `CRYPT_KEEPER_ALLOWED_HOSTS` | `*.run.app,*.fly.dev,localhost,127.0.0.1` | Host headers accepted. Add a custom domain here. |
| `CRYPT_KEEPER_ALLOWED_ORIGINS` | local dev origins | Extra browser origins allowed to POST. The site's own origin is always allowed. |
| `CRYPT_KEEPER_MAX_PROOFS` | `4` | Concurrent sandbox runs. A `/revive` uses two. |
| `CRYPT_KEEPER_MAX_EXHUMES` | `8` | Concurrent downloads and unpackings. Each holds an archive in memory. |
| `CRYPT_KEEPER_CHILD_MEM_MB` | `3072` | Address-space limit for each sandboxed process (Linux). `0` disables it. |
| `CRYPT_KEEPER_ALLOW_SOURCE_BUILDS` | off | Local use only: build a dependency with no wheel from source (runs its `setup.py`). Ignored while `CRYPT_KEEPER_PUBLIC` is on, so it cannot be switched on for a public host by mistake. |

To change one after deploying (the allowlist contains commas, so use gcloud's alternate delimiter;
see `gcloud topic escaping`):

```powershell
gcloud run services update crypt-keeper --region us-central1 `
  --update-env-vars "^@^CRYPT_KEEPER_PROOF_ALLOWLIST=attrdict,hyper,unittest2"
gcloud run services update crypt-keeper --region us-central1 --update-env-vars CRYPT_KEEPER_PROOF_OF_LIFE=off
```

### If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| "origin not allowed" when you press Raise it | The platform did not send `X-Forwarded-Proto`. Set `CRYPT_KEEPER_ALLOWED_ORIGINS` to `https://YOUR-SERVICE-URL`. |
| `400 Invalid host header` | Add the host to `CRYPT_KEEPER_ALLOWED_HOSTS` (for a custom domain). |
| Stages do not tick; everything appears at the end | The response is being buffered. The result is still correct. |
| A run fails or the instance restarts | Out of memory. Raise `--memory` or lower `--concurrency`. |
| `503 The crypt is busy` | All sandbox or exhume slots are in use. Wait, or raise `CRYPT_KEEPER_MAX_PROOFS` and memory together. |
| A listed package shows "dependencies have not been reviewed" | Its dependencies changed, or it has no entry in `approved_dependencies.json`. Run `scripts/review_dependencies.py PACKAGE`, read the output, then `--write` and rebuild. |
| Every verdict reads "Proof of life is not enabled for any package" | The allow-list is missing or blank, and the service is failing closed on purpose. Set `CRYPT_KEEPER_PROOF_ALLOWLIST` again (see the command above). |
| `413 ... too large to exhume` | The package's source archive is over a limit (20 MB download, 20,000 entries or 250 MB unpacked). It is refused whole, not partly analysed. |
| GitHub repositories fail with "rate limit" | Unauthenticated GitHub allows few requests per hour. The curated demo needs only PyPI. |

## Azure Container Apps (tested)

Deployed and checked with `scripts/smoke_test.py` for `attrdict` and `hyper`: all checks pass, progress
streams as separate events, and an unlisted package is not executed. Not tested: behaviour of runs longer
than the ingress timeout (240 s by default), which the listed packages never reach (10 to 20 s).

`az containerapp up --source .` crashed on Windows with `'charmap' codec can't encode characters`: the CLI
streams the build log through a console that cannot print some of its characters. Forcing UTF-8 did not
help. Build without streaming the log instead:

```powershell
az group create -n crypt-keeper-rg -l eastus2
az containerapp env create -n crypt-keeper-env -g crypt-keeper-rg -l eastus2
az acr create -n <uniqueregistryname> -g crypt-keeper-rg --sku Basic
az acr build -r <uniqueregistryname> -t crypt-keeper:v1 --no-logs .      # prints a run ID, e.g. ch3
az acr task show-run -r <uniqueregistryname> --run-id ch3 --query status -o tsv   # repeat until Succeeded

az acr update -n <uniqueregistryname> --admin-enabled true
$u = az acr credential show -n <uniqueregistryname> --query username -o tsv
$p = az acr credential show -n <uniqueregistryname> --query "passwords[0].value" -o tsv
az containerapp create -n crypt-keeper -g crypt-keeper-rg --environment crypt-keeper-env `
  --image <uniqueregistryname>.azurecr.io/crypt-keeper:v1 `
  --registry-server <uniqueregistryname>.azurecr.io --registry-username $u --registry-password $p `
  --ingress external --target-port 8080 --cpu 1 --memory 2Gi --min-replicas 0 --max-replicas 2 `
  --env-vars "CRYPT_KEEPER_ALLOWED_HOSTS=*.azurecontainerapps.io,localhost,127.0.0.1"
python scripts/smoke_test.py https://<fqdn printed by the last command>
```

**What is deployed lags the repository.** The first Azure deployment was built before the zip download
(`POST /revive/export`), the source-encoding fix and the `setup.py` dependency fix. Until you build and update the
image (`az acr build ... -t crypt-keeper:v2 --no-logs .`, wait for `Succeeded`, then `az containerapp update ... --image ...:v2`),
the live page has no download button. The new image was checked locally in public mode: all four listed packages give
the same verdicts, and a downloaded patch applied cleanly to attrdict's original source.

Notes:
- The app has **no managed identity** (`identity: None`), so code in the container has no cloud token to
  ask for. Do not add one. The registry pull uses the registry's admin credentials, kept as an app secret.
- `CRYPT_KEEPER_ALLOWED_HOSTS` must include `*.azurecontainerapps.io`, or every request is a 400.
- `--min-replicas 0` costs nothing when idle; the first request after a pause is slow.
- The registry (Basic SKU) has a small fixed monthly cost. To remove everything:
  `az group delete -n crypt-keeper-rg`.
- To ship a new version: `az acr build ... -t crypt-keeper:v2 --no-logs .`, wait for it, then
  `az containerapp update -n crypt-keeper -g crypt-keeper-rg --image <registry>.azurecr.io/crypt-keeper:v2`.

## Fly.io (alternative, not tested)

Fly builds the same `Dockerfile`. Roughly: install `flyctl`, run `fly launch --no-deploy` in the
repository root, set the internal port to `8080` and a 2 GB memory size in the generated
`fly.toml`, enable auto-stop for machines, then `fly deploy`. Then run `scripts/smoke_test.py`
against the `*.fly.dev` URL. The image already allows `*.fly.dev` hosts.

## Run the image locally

```powershell
docker build -t crypt-keeper .
docker run --rm -p 8080:8080 crypt-keeper
python scripts/smoke_test.py http://127.0.0.1:8080
```

## Not done

- A recorded-run fallback (replaying saved results if the live host is down).
- Per-IP rate limiting, and running the sandboxed child as a separate user.
- Hash-pinned dependencies for the listed packages (their transitive dependencies are not reviewed).
- Running arbitrary packages safely for the public. That needs real isolation per run, such as
  [Cloud Run sandboxes](https://docs.cloud.google.com/run/docs/code-execution) (Preview) or a remote
  sandbox service, and is a larger change than this demo needs.
