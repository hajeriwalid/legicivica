"""
Record what actually produced a stored law's analysis.

Why this exists: the agents deliberately run against floating model aliases
(`gemini-pro-latest`, `gemini-flash-latest`) so that new laws are analysed by
the best model available at the time. That is a deliberate choice — see
ROADMAP.md #3(a) — and it is the right one, but it means the model underneath
a score changes without any code change or deploy.

The risk was never that the model changes. It is not knowing *which* model
produced a given score. So instead of pinning, we record: every stored law
carries the resolved model id per agent, a fingerprint of the prompts, and
how complete its reference resolution actually was.

Three things this makes possible:
  - answering "what produced this score?" six months after the fact;
  - spotting that a jump in the score-evolution chart lines up with a model
    change rather than a change in legislation;
  - telling a reader when an explanation rests on a partial view of the law.

No ADK or network dependency beyond reading the agent definitions.
"""

import hashlib

from legicivica.agents.pipeline import (
    civic_agent,
    classifier_agent,
    explainer_agent,
    law_fetcher,
)

# Bump when the *shape* of a stored record changes, so old and new documents
# can be told apart without guessing from which keys happen to be present.
PIPELINE_VERSION = 2

# Agents whose prompts feed the fingerprint below. law_fetcher is included
# even though the poller path doesn't invoke it — a change to it still
# changes pipeline behaviour for the conversational entrypoint.
_FINGERPRINTED_AGENTS = (law_fetcher, explainer_agent, classifier_agent, civic_agent)


def prompt_fingerprint() -> str:
    """
    Short stable hash over every agent's name + instruction text.

    Derived rather than hand-maintained on purpose: a manually bumped
    PROMPT_VERSION is exactly the kind of thing that silently goes stale after
    someone tweaks a prompt, which would make the provenance actively
    misleading — worse than absent. This changes whenever the prompts do.
    """
    digest = hashlib.sha256()
    for agent in _FINGERPRINTED_AGENTS:
        digest.update((getattr(agent, "name", "") or "").encode("utf-8"))
        digest.update(b"\x00")
        digest.update((getattr(agent, "instruction", "") or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:12]


def configured_models() -> dict[str, str]:
    """The alias each agent is configured with, e.g. {"explainer": "gemini-pro-latest"}."""
    return {
        getattr(a, "name", f"agent_{i}"): str(getattr(a, "model", "") or "")
        for i, a in enumerate(_FINGERPRINTED_AGENTS)
    }


def collect_model_versions(events: list) -> dict[str, str]:
    """
    Map agent name -> the model id that actually answered, from ADK events.

    `Event.model_version` resolves the floating alias to a concrete build
    (verified 2026-10-06: `gemini-flash-latest` -> `gemini-3.8-flash`), which
    is the whole point — the alias alone records nothing useful.

    Last write wins per author: if an agent emits several events the final
    one reflects the model that produced its completed output.
    """
    resolved: dict[str, str] = {}
    for event in events:
        author = getattr(event, "author", None)
        version = getattr(event, "model_version", None)
        if author and version:
            resolved[str(author)] = str(version)
    return resolved


def summarize_usage(events: list) -> dict:
    """
    Total token usage across an invocation, when ADK reports it.

    Not billing-grade — it misses retries the runner swallows — but enough to
    notice a prompt change that doubles cost, which is the realistic use.
    """
    prompt_tokens = 0
    response_tokens = 0
    total_tokens = 0
    seen = False
    for event in events:
        usage = getattr(event, "usage_metadata", None)
        if not usage:
            continue
        seen = True
        prompt_tokens += getattr(usage, "prompt_token_count", 0) or 0
        response_tokens += getattr(usage, "candidates_token_count", 0) or 0
        total_tokens += getattr(usage, "total_token_count", 0) or 0
    if not seen:
        return {}
    return {
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "total_tokens": total_tokens,
    }


def summarize_coverage(resolver_result: dict | None) -> dict:
    """
    How much of the law's reference graph the explanation actually rests on.

    `resolve_law_references` is bounded by `max_depth` / `max_articles` and
    already tracks precisely what it skipped — but the poller used to discard
    `resolver_result` entirely, so a law referencing 60 articles of which 25
    were resolved produced an explanation built on 40% of the picture, with
    nothing anywhere saying so.

    `complete` is the field worth surfacing: False means the explanation is
    known to be partial, not merely that something failed.
    """
    if not resolver_result:
        return {}

    resolved = len(resolver_result.get("resolved") or [])
    errors = len(resolver_result.get("errors") or [])
    skipped_depth = len(resolver_result.get("skipped_max_depth") or [])
    skipped_budget = len(resolver_result.get("skipped_max_articles") or [])
    corrections = len(resolver_result.get("corrections") or [])
    discovered = resolved + errors + skipped_depth + skipped_budget

    ratio = round(resolved / discovered, 3) if discovered else None

    return {
        "references_discovered": discovered,
        "articles_resolved": resolved,
        "articles_failed": errors,
        "skipped_max_depth": skipped_depth,
        "skipped_max_articles": skipped_budget,
        "corrections": corrections,
        "coverage_ratio": ratio,
        # Deliberately NOT called "complete". An earlier draft used that name
        # for "nothing was truncated", which is a real distinction — a failed
        # fetch means we looked and missed, truncation means we never looked —
        # but it produced actively misleading records: the first real run
        # emitted `complete: true` alongside a coverage ratio of 0.298,
        # because 33 of 47 references failed to fetch while nothing was
        # truncated. A reader would take "complete" to mean the explanation
        # rests on the whole law. These two fields now say what they mean.
        "not_truncated": (skipped_depth + skipped_budget) == 0,
        "low_coverage": ratio is not None and ratio < 0.5,
        "params": resolver_result.get("params") or {},
    }


def build_provenance(events: list, resolver_result: dict | None) -> dict:
    """Assemble the full provenance block stored on a law record."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "prompt_fingerprint": prompt_fingerprint(),
        "models_configured": configured_models(),
        "models_resolved": collect_model_versions(events),
        "usage": summarize_usage(events),
        "coverage": summarize_coverage(resolver_result),
    }
