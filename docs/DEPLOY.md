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
- `/proof-of-life`, which runs source the caller posts, returns 403 while a list is set.

Do not add a package to the list unless you have looked at it. The code that runs is the latest
PyPI release of each listed package.

Other things to know:

- **No secrets in the service environment.** On Linux a child process running as the same user can
  read its parent's environment through `/proc`. The sandbox scrubs the child's own environment,
  not that. Do not set tokens on this service.
- The container has outbound internet access (the sandbox needs it for `pip`).
- Turn execution off completely with `CRYPT_KEEPER_PROOF_OF_LIFE=off`.
- There is no per-IP rate limit. Cost and load are bounded only by `--max-instances` and the slot
  limit (`CRYPT_KEEPER_MAX_PROOFS`). A small archive can expand a lot while it is scanned. Both are
  acceptable for a demo and worth fixing before a real launch.

## What was and was not tested

Tested here, on the real image (built from this `Dockerfile`, 182 MB, Python 3.12 inside):

- the image builds and starts, and the UI loads from it
- `scripts/smoke_test.py` passes all 11 checks against it: health, Python 3.12 present, a full
  streamed `attrdict` run (`resurrected` in about 8 s), progress events arriving one by one, an
  unlisted package (`docopt`) getting an autopsy but **no execution**, and a posted-source request
  refused with 403
- the proxy and host rules, using the headers a platform sends: https origin with
  `X-Forwarded-Proto: https` is accepted; a missing header, an http page against an https site, a
  foreign origin and an unknown Host are refused; a `*.run.app` host is accepted
- the UI against the container, including the curated-list notice and the skipped stages

**Not tested** (no `gcloud` or `flyctl` here, and no cloud account):

- the deploy commands below, which are written from the Cloud Run documentation
- streaming through Cloud Run's front end. It is reported to work for chunked responses without
  special settings, but I have not seen it. If it buffers, the UI still works, but the progress
  list jumps to done at the end.
- cold-start time and memory sizing under real traffic

Run `scripts/smoke_test.py` against the deployed URL first. It tells you which of these held.

## Cloud Run

You need a Google account, a project with billing enabled, and the
[gcloud CLI](https://cloud.google.com/sdk/docs/install). New customers get free credit, and Cloud Run
scales to zero when idle.

```powershell
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# from the repository root (where the Dockerfile is)
gcloud run deploy crypt-keeper `
  --source . `
  --region us-central1 `
  --allow-unauthenticated `
  --cpu 2 --memory 2Gi `
  --timeout 900 `
  --concurrency 4 `
  --max-instances 3
```

The first run asks to enable the Cloud Build, Artifact Registry and Cloud Run APIs and to create a
repository. Answer yes. It builds the image remotely, so you do not need Docker. When it finishes it
prints the **Service URL**. Then check it:

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
| `CRYPT_KEEPER_PROOF_ALLOWLIST` | `attrdict,hyper,unittest2,flask-restplus` | Packages that may be executed. Empty means all (local use only). |
| `CRYPT_KEEPER_PROOF_OF_LIFE` | on | `off` disables execution entirely. |
| `CRYPT_KEEPER_ALLOWED_HOSTS` | `*.run.app,*.fly.dev,localhost,127.0.0.1` | Host headers accepted. Add a custom domain here. |
| `CRYPT_KEEPER_ALLOWED_ORIGINS` | local dev origins | Extra browser origins allowed to POST. The site's own origin is always allowed. |
| `CRYPT_KEEPER_MAX_PROOFS` | `4` | Concurrent sandbox runs. A `/revive` uses two. |

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
| `503 The crypt is busy` | All sandbox slots are in use. Wait, or raise `CRYPT_KEEPER_MAX_PROOFS` and memory together. |
| GitHub repositories fail with "rate limit" | Unauthenticated GitHub allows few requests per hour. The curated demo needs only PyPI. |

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
- Per-IP rate limiting and a decompression cap.
- Running arbitrary packages safely for the public. That needs real isolation per run, such as
  [Cloud Run sandboxes](https://docs.cloud.google.com/run/docs/code-execution) (Preview) or a remote
  sandbox service, and is a larger change than this demo needs.
