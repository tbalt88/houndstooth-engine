"""Shared Claude API client -- exactly one per process.

The anthropic SDK resolves credentials when a client is constructed. Locally that is
ANTHROPIC_API_KEY; in GitHub Actions it is Workload Identity Federation (see
daily.yml), where every client construction performs a token exchange and each
GitHub OIDC token may be exchanged only once. A client per call -- which score.py
and drafter.py used to do -- would re-present the same single-use token and be
rejected as a replay (reason `jti_reused` in the Console's authentication history).
"""
import functools


@functools.lru_cache(maxsize=1)
def client():
    import anthropic
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY locally; federation env vars in CI
