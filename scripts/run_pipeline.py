import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from legicivica.agents.orchestrator import impact_pipeline
from legicivica.agents.translator import translate_law_to_french
from legicivica.storage.firestore_store import (
    get_poll_state,
    law_exists,
    list_laws,
    save_implementation,
    save_law,
    set_poll_state,
)
from legicivica.tools.decree_tracker import (
    extract_decree_deferrals,
    implementation_status,
    law_number_from_title,
    match_implementing_texts,
    merge_implementing_texts,
    summarize_deferrals,
    sweep_jorf_texts,
)
from legicivica.tools.legifrance import fetch_law_text, search_jorf_by_date_range

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("run_pipeline")

APP_NAME = "legicivica-pipeline"
USER_ID = "poller"


def _to_jsonable(value):
    """
    Recursively convert Pydantic model instances to plain dicts.

    build_transparency_report's returned dict embeds classification's
    affected_parties as actual AffectedParty model instances (not dicts) —
    it only extracts primitive fields for its other components, so this
    one field slips through as-is. Rather than patch scoring.py (outside
    this phase's scope — it's existing, tested code), this walks whatever
    structure impact_pipeline hands back and converts anything Pydantic
    into something the Firestore client actually knows how to serialize.
    """
    if isinstance(value, BaseModel):
        return _to_jsonable(value.model_dump())
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


async def process_one_law(jorf_id: str) -> dict:
    """
    Run impact_pipeline on a single law and return a Firestore-ready record.

    Raises on failure — the caller is responsible for catching per-law
    errors so one bad law doesn't abort the whole batch (see main()).
    """
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=impact_pipeline, app_name=APP_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=jorf_id)])

    final_output = None
    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        if event.output is not None:
            final_output = event.output

    if final_output is None:
        raise RuntimeError(f"impact_pipeline produced no output for {jorf_id}")

    # Re-fetch the session rather than trusting the pre-run object's
    # identity — InMemorySessionService may hold a separate internal copy
    # that the Runner actually mutates (verified against ADK source while
    # planning this). This is how we read the law's summary without
    # touching orchestrator.py: explainer_agent's output_key already put
    # it in workflow state, stored as a plain dict (model_dump), so this
    # is a dict field access, not attribute access.
    session = await session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    explanation_state = session.state.get("explanation", {}) or {}

    transparency = _to_jsonable(final_output["transparency"])
    civic = _to_jsonable(final_output["civic"])

    # Décret d'application tracking. Deliberately re-fetches the law text
    # rather than reading it out of workflow state: the resolver's `root`
    # is shaped for reference resolution, and depending on its internals
    # would couple this feature to orchestrator changes. One extra API
    # call per law, on a path that already makes many.
    try:
        law_text = fetch_law_text(jorf_id)
        full_text = " ".join(a.get("content", "") for a in law_text.get("articles", []))
        deferrals = extract_decree_deferrals(full_text)
        law_title = transparency.get("law_title", "") or law_text.get("title", "")
        implementation = {
            **summarize_deferrals(deferrals),
            "law_number": law_number_from_title(law_title),
            "deferrals": deferrals,
            "implementing_texts": [],
            **implementation_status(deferrals, []),
        }
    except Exception:
        logger.exception("deferral extraction failed for %s — saving law without it", jorf_id)
        implementation = None

    return {
        "jorf_id": jorf_id,
        "title": transparency.get("law_title", ""),
        "publication_date": session.state.get("resolver_result", {}).get("root", {}).get("date", ""),
        "summary": explanation_state.get("summary", ""),
        "transparency": transparency,
        "civic": civic,
        "affected_parties": transparency.get("affected_parties", []),
        "eu_directives_referenced": transparency.get("eu_directives_referenced", []),
        "implementation": implementation,
    }


def _date_window(mode: str, weeks: int) -> tuple[str, str]:
    today = date.today()
    if mode == "backfill":
        start = today - timedelta(weeks=weeks)
    else:
        last_checked = get_poll_state()
        if last_checked:
            start = date.fromisoformat(last_checked) - timedelta(days=2)  # lag buffer
        else:
            # First-ever daily run with no watermark: don't silently
            # process an unbounded history — fall back to a short, safe
            # lookback instead.
            start = today - timedelta(days=3)
    return start.isoformat(), today.isoformat()


def reconcile_implementations(lookback_weeks: int = 12) -> tuple[int, int]:
    """
    Re-check every stored law for implementing texts published since.

    This is the half of décret tracking that cannot happen at ingest time: a
    law published today has no implementing décret yet, and may not for
    months or years. So the poller sweeps recent JORF décrets/arrêtés on
    every run and reconciles them against every law already tracked.

    Sweeping is batched deliberately — one sweep of the window, matched
    against all laws at once — because the sweep is the expensive part
    (one API call per 100 texts, doubled while _get_token() stays uncached).
    Per-law sweeps would multiply that by the size of the collection.

    Results accumulate and are never removed (see decree_tracker's note on
    unstable pagination), so repeated runs can only improve coverage.

    Returns:
        (laws_examined, laws_updated)
    """
    laws = list_laws()
    if not laws:
        return 0, 0

    end = date.today()
    start = end - timedelta(weeks=lookback_weeks)
    logger.info("reconcile: sweeping JORF %s..%s for implementing texts", start, end)
    candidates = sweep_jorf_texts(start.isoformat(), end.isoformat())
    logger.info("reconcile: %d candidate text(s) enumerated", len(candidates))

    # Map law number -> jorf ids. Numbers are not perfectly unique: a
    # rectificatif can share its law's number (observed with n° 2026-668,
    # which appears twice in JORF), so one number may fan out to several docs.
    by_number: dict[str, list[dict]] = {}
    backfilled = 0
    for law in laws:
        impl = law.get("implementation") or {}

        # Laws processed before décret tracking existed have no deferrals
        # recorded. Extract them now, once, so historical laws get the same
        # treatment as new ones rather than silently rendering as blank.
        if "deferrals" not in impl:
            try:
                text = fetch_law_text(law["jorf_id"])
                full = " ".join(a.get("content", "") for a in text.get("articles", []))
                deferrals = extract_decree_deferrals(full)
                impl = {
                    **impl,
                    **summarize_deferrals(deferrals),
                    "deferrals": deferrals,
                }
                law["implementation"] = impl
                backfilled += 1
            except Exception:
                logger.exception("reconcile: deferral backfill failed for %s", law.get("jorf_id"))

        number = impl.get("law_number") or law_number_from_title(law.get("title", ""))
        if number:
            by_number.setdefault(number, []).append(law)
    if backfilled:
        logger.info("reconcile: backfilled deferrals for %d law(s)", backfilled)

    matches = match_implementing_texts(list(by_number), candidates) if by_number else {}

    # Iterate over every law, not just those in `matches`. A law whose title
    # carries no parseable "n° YYYY-NNN" can never match an implementing
    # text, but it can still have deferrals worth recording — and if it is
    # skipped here, the backfill above is recomputed and thrown away on
    # every single run.
    updated = 0
    for law in laws:
        impl = dict(law.get("implementation") or {})
        if "deferrals" not in impl:
            continue  # backfill failed for this law; nothing to persist

        number = impl.get("law_number") or law_number_from_title(law.get("title", ""))
        found = matches.get(number, []) if number else []
        merged = merge_implementing_texts(impl.get("implementing_texts"), found)

        if len(merged) == len(impl.get("implementing_texts") or []) and impl.get("status"):
            continue  # nothing new, and already assessed (status persisted)

        deferrals = impl.get("deferrals") or []
        impl.update({
            "law_number": number,
            "implementing_texts": merged,
            **implementation_status(deferrals, merged),
        })
        try:
            save_implementation(law["jorf_id"], impl)
            updated += 1
            if merged:
                logger.info(
                    "reconcile: loi n° %s -> %d implementing text(s) [%s]",
                    number, len(merged), impl["status"],
                )
        except Exception:
            logger.exception("reconcile: failed to save %s", law.get("jorf_id"))

    return len(laws), updated


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["backfill", "daily", "reconcile"], required=True)
    parser.add_argument(
        "--reconcile-weeks", type=int, default=12,
        help="How far back to sweep JORF for implementing texts (default 12).",
    )
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--cap", type=int, default=20)
    args = parser.parse_args()

    if args.mode == "reconcile":
        examined, updated = reconcile_implementations(args.reconcile_weeks)
        logger.info("done: %d law(s) examined, %d updated", examined, updated)
        return 0

    start_date, end_date = _date_window(args.mode, args.weeks)
    logger.info("mode=%s window=%s..%s cap=%s", args.mode, start_date, end_date, args.cap)

    try:
        candidates = search_jorf_by_date_range(start_date, end_date, nature="LOI")
    except Exception:
        logger.exception("JORF search failed — aborting batch")
        return 1

    new_ids = [c for c in candidates if not law_exists(c["id"])]
    new_ids = new_ids[: args.cap]
    logger.info("%d candidate(s) found, %d new after dedup, capped to %d", len(candidates), len(new_ids), len(new_ids))

    processed, failed = 0, 0
    for candidate in new_ids:
        jorf_id = candidate["id"]
        try:
            record = await process_one_law(jorf_id)
            record["pipeline_run_mode"] = args.mode
            try:
                record["fr"] = await translate_law_to_french(record)
            except Exception:
                logger.exception("French translation failed for %s — saving English-only, will retry via backfill", jorf_id)
            save_law(record)
            processed += 1
            logger.info("saved %s — %s", jorf_id, record["title"][:80])
        except Exception:
            failed += 1
            logger.exception("failed to process %s — skipping, will retry next run", jorf_id)

    if args.mode == "daily":
        set_poll_state(end_date)
        try:
            examined, updated = reconcile_implementations(args.reconcile_weeks)
            logger.info("reconcile: %d law(s) examined, %d updated", examined, updated)
        except Exception:
            logger.exception("reconciliation failed — poll itself succeeded, will retry next run")

    logger.info("done: %d processed, %d failed", processed, failed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
