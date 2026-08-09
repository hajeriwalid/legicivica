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
from legicivica.storage.firestore_store import (
    get_poll_state,
    law_exists,
    save_law,
    set_poll_state,
)
from legicivica.tools.legifrance import search_jorf_by_date_range

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

    return {
        "jorf_id": jorf_id,
        "title": transparency.get("law_title", ""),
        "publication_date": session.state.get("resolver_result", {}).get("root", {}).get("date", ""),
        "summary": explanation_state.get("summary", ""),
        "transparency": transparency,
        "civic": civic,
        "affected_parties": transparency.get("affected_parties", []),
        "eu_directives_referenced": transparency.get("eu_directives_referenced", []),
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


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["backfill", "daily"], required=True)
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--cap", type=int, default=20)
    args = parser.parse_args()

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
            save_law(record)
            processed += 1
            logger.info("saved %s — %s", jorf_id, record["title"][:80])
        except Exception:
            failed += 1
            logger.exception("failed to process %s — skipping, will retry next run", jorf_id)

    if args.mode == "daily":
        set_poll_state(end_date)

    logger.info("done: %d processed, %d failed", processed, failed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
