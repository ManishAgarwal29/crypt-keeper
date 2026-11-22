"""Pytest config: make backend modules importable as top-level packages."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_policy_environment(monkeypatch):
    """Tests start as a plain local run, whatever the shell or a container sets."""
    monkeypatch.delenv("CRYPT_KEEPER_PROOF_ALLOWLIST", raising=False)
    monkeypatch.delenv("CRYPT_KEEPER_PUBLIC", raising=False)

# The property tests check logic, not speed. Hypothesis's `too_slow` health check and per-example
# deadline fire on a busy machine (the suite also builds virtualenvs) without any real defect,
# so they are off. An explicit `--hypothesis-profile=deep` still overrides this.
from hypothesis import HealthCheck, settings  # noqa: E402

settings.register_profile("crypt-keeper", suppress_health_check=[HealthCheck.too_slow], deadline=None)
# Used by the save hook: a quick sanity pass, not the real run.
settings.register_profile("fast", parent=settings.get_profile("crypt-keeper"), max_examples=25)
settings.load_profile("crypt-keeper")
