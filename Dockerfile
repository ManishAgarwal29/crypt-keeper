# Crypt Keeper for a public host (Cloud Run, Fly.io, ...). Build from the repository root.
#
# This image runs package code during proof of life. By default it only does so for the curated
# list below (CRYPT_KEEPER_PROOF_ALLOWLIST); every other package gets the autopsy and revival
# without being executed. See docs/DEPLOY.md before changing that.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

# Run as an ordinary user. The sandbox builds its virtualenvs under /tmp.
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
WORKDIR /app/backend

# Hosts the app accepts (Starlette wildcard patterns). Add your own domain with
# CRYPT_KEEPER_ALLOWED_HOSTS at deploy time. The curated list is what stops strangers from
# making the server execute arbitrary packages.
ENV CRYPT_KEEPER_ALLOWED_HOSTS="*.run.app,*.fly.dev,localhost,127.0.0.1" \
    CRYPT_KEEPER_PROOF_ALLOWLIST="attrdict,hyper,unittest2,flask-restplus" \
    CRYPT_KEEPER_MAX_PROOFS=4

# Cloud Run sets $PORT (8080). --proxy-headers makes the app trust the platform's
# X-Forwarded-* headers, which it needs to see that the browser used https.
EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
