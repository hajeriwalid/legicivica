from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.schemas import LawTranslationFR

_APP_NAME = "legicivica-pipeline"
_USER_ID = "poller"

# Flash, not Pro — this is pure translation of already-generated text, not a
# new reasoning task, so it doesn't need the heavier model the explainer/
# classifier/civic agents use. Runs standalone (not wired into orchestrator.py's
# Workflow graph): translation is a mechanical pass over a finished record, not
# part of the resolve -> explain -> classify -> civic reasoning chain.
translator_agent = LlmAgent(
    name="translator_fr",
    model="gemini-flash-latest",
    output_schema=LawTranslationFR,
    instruction="""
    You are a professional French legal-translation assistant. You will be
    given the English-language output of an AI pipeline that already analyzed
    a French law: a plain-language summary, score-component reasons, and
    affected-party descriptions.

    Translate every free-text field into natural, fluent French — the kind a
    French civic-tech publication would actually publish, not a literal
    word-for-word translation. Use correct French legal terminology where a
    term has a standard French equivalent.

    Do not re-analyze, re-score, add information, or omit information — this
    is a translation pass only. Every label/category field marked "unchanged"
    in the schema must be copied back exactly as given, so the translation can
    be matched to the right item.
    """,
)


def build_translation_prompt(record: dict) -> str:
    """
    Build the input text for translator_agent from an already-assembled,
    English-language law record (the same shape process_one_law() returns).
    """
    transparency = record.get("transparency", {}) or {}
    civic = record.get("civic", {}) or {}

    lines = [
        f"LAW ID: {record.get('jorf_id', '')}",
        f"LAW TITLE (already in French, do not translate): {record.get('title', '')}",
        "",
        "=== Summary to translate ===",
        record.get("summary", ""),
        "",
        "=== Transparency score component reasons to translate ===",
    ]
    for c in transparency.get("components", []):
        lines.append(f"- label: {c.get('label')}")
        lines.append(f"  reason: {c.get('reason')}")

    lines.append("")
    lines.append("=== Civic index criteria reasons to translate ===")
    for c in civic.get("criteria", []):
        lines.append(f"- label: {c.get('label')}")
        lines.append(f"  reason: {c.get('reason')}")

    lines.append("")
    lines.append("=== Affected parties to translate ===")
    for p in record.get("affected_parties", []):
        lines.append(f"- category: {p.get('category')}")
        lines.append(f"  obligation: {p.get('obligation')}")

    return "\n".join(lines)


def _merge_translation(record: dict, translation: LawTranslationFR) -> dict:
    """
    Turn a validated LawTranslationFR into the fr sub-document stored on the
    law record — parallel lists in the same order as the English
    transparency.components / civic.criteria / affected_parties lists, so the
    dashboard can zip() them together at render time instead of doing its own
    label matching.

    Matches translated items back to English ones by label/category text
    (not list position — the model isn't guaranteed to preserve order).
    Falls back to the English text for anything that doesn't find a match,
    so a partial or slightly-off translation degrades gracefully instead of
    dropping content.
    """
    reasons_by_label = {r.label: r.reason_fr for r in translation.transparency_reasons_fr}
    civic_reasons_by_label = {r.label: r.reason_fr for r in translation.civic_reasons_fr}
    parties_by_category = {p.category_en: p for p in translation.affected_parties_fr}

    transparency_reasons_fr = [
        reasons_by_label.get(c["label"], c["reason"])
        for c in record.get("transparency", {}).get("components", [])
    ]
    civic_reasons_fr = [
        civic_reasons_by_label.get(c["label"], c["reason"])
        for c in record.get("civic", {}).get("criteria", [])
    ]
    affected_parties_fr = []
    for p in record.get("affected_parties", []):
        match = parties_by_category.get(p["category"])
        affected_parties_fr.append({
            "category_fr": match.category_fr if match else p["category"],
            "obligation_fr": match.obligation_fr if match else p["obligation"],
        })

    return {
        "summary": translation.summary_fr,
        "transparency_reasons": transparency_reasons_fr,
        "civic_reasons": civic_reasons_fr,
        "affected_parties": affected_parties_fr,
    }


async def translate_law_to_french(record: dict) -> dict:
    """
    Run translator_agent over an already-built English record and return the
    fr sub-document to merge into it.

    Raises on failure — treated as best-effort by callers: a translation
    failure should never block saving the English record, since the daily
    poller must keep making progress even if this one extra step has a bad
    day (see run_pipeline.py's main()).
    """
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=_APP_NAME, user_id=_USER_ID)
    runner = Runner(agent=translator_agent, app_name=_APP_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=build_translation_prompt(record))])

    raw_output = None
    async for event in runner.run_async(user_id=_USER_ID, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    raw_output = part.text

    if raw_output is None:
        raise RuntimeError(f"translator_agent produced no output for {record.get('jorf_id')}")

    translation = LawTranslationFR.model_validate_json(raw_output)
    return _merge_translation(record, translation)
