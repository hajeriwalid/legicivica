"""
Run the dashboard locally with REAL décret-tracking data and no cloud setup.

Why this exists: the dashboard normally reads Firestore, which needs ADC
(`gcloud auth application-default login`) — and even with it, production
laws have no `implementation` field until a reconcile pass has run. So
there would be nothing of the new feature to look at.

This harness fetches real laws from Légifrance, runs the real deferral
extractor and the real implementing-text sweep, and stubs only the parts
that would otherwise cost money or need credentials:
  - no Firestore  (records are held in memory)
  - no Gemini     (scores/summaries are placeholders, clearly labelled)

Needs only PISTE credentials, which are already in .env.

Run:
    PYTHONPATH=. streamlit run scripts/preview_dashboard.py
"""

import datetime

from dotenv import load_dotenv

load_dotenv()

import app.streamlit_app as dashboard  # noqa: E402
from legicivica.tools.decree_tracker import (  # noqa: E402
    extract_decree_deferrals,
    implementation_status,
    law_number_from_title,
    match_implementing_texts,
    summarize_deferrals,
    sweep_jorf_texts,
)
from legicivica.tools.legifrance import (  # noqa: E402
    fetch_law_text,
    search_jorf_by_date_range,
)

PLACEHOLDER = "[preview build — no Gemini call made, this text is a placeholder]"
LOOKBACK_WEEKS = 10
SWEEP_WEEKS = 8


def _stub_scores(seed: int) -> tuple[dict, dict]:
    """Deterministic filler so the chart and cards render — not real analysis."""
    transparency = {
        "overall_score": 3,
        "overall_max": 5,
        "components": [
            {"label": "Delegation ratio", "score": 3, "reason": PLACEHOLDER},
            {"label": "Reference clarity", "score": 3, "reason": PLACEHOLDER},
            {"label": "Effective date clarity", "score": 3, "reason": PLACEHOLDER},
        ],
        "affected_parties": [],
        "eu_directives_referenced": [],
    }
    civic = {
        "civic_index": (seed % 5) - 2,
        "civic_index_range": "-10 to +10",
        "criteria": [{"label": "Rule of law", "score": 0, "reason": PLACEHOLDER}],
        "notice": PLACEHOLDER,
    }
    return transparency, civic


def build_preview_records() -> list[dict]:
    today = datetime.date.today()
    laws = search_jorf_by_date_range(
        (today - datetime.timedelta(weeks=LOOKBACK_WEEKS)).isoformat(),
        today.isoformat(),
        nature="LOI",
        page_size=50,
    )
    print(f"[preview] {len(laws)} law(s) fetched")

    candidates = sweep_jorf_texts(
        (today - datetime.timedelta(weeks=SWEEP_WEEKS)).isoformat(),
        today.isoformat(),
    )
    print(f"[preview] {len(candidates)} candidate implementing text(s) swept")

    records = []
    numbers = []
    for law in laws:
        number = law_number_from_title(law.get("title", ""))
        numbers.append(number)
        try:
            text = fetch_law_text(law["id"])
            full = " ".join(a.get("content", "") for a in text.get("articles", []))
            deferrals = extract_decree_deferrals(full)
        except Exception as exc:  # noqa: BLE001
            print(f"[preview] skipped {law['id']}: {exc}")
            continue

        transparency, civic = _stub_scores(len(records))
        records.append({
            "jorf_id": law["id"],
            "title": law.get("title", ""),
            "publication_date": law.get("date", ""),
            "summary": PLACEHOLDER,
            "transparency": transparency,
            "civic": civic,
            "affected_parties": [],
            "eu_directives_referenced": [],
            "implementation": {
                **summarize_deferrals(deferrals),
                "law_number": number,
                "deferrals": deferrals,
                "implementing_texts": [],
            },
        })

    matched = match_implementing_texts([n for n in numbers if n], candidates)
    for record in records:
        impl = record["implementation"]
        found = matched.get(impl.get("law_number") or "", [])
        impl["implementing_texts"] = found
        impl.update(implementation_status(impl.get("deferrals") or [], found))

    counts: dict[str, int] = {}
    for r in records:
        counts[r["implementation"]["status"]] = counts.get(r["implementation"]["status"], 0) + 1
    print(f"[preview] statuses: {counts}")
    return records


_CACHE: list[dict] = []


def _load_preview() -> list[dict]:
    global _CACHE
    if not _CACHE:
        _CACHE = build_preview_records()
    return _CACHE


dashboard._load_records = _load_preview
dashboard.render()
