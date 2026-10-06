"""
Smoke test for décret d'application tracking.

Two halves, matching decree_tracker.py:
  1. Deferral extraction — pure text, no API. Asserted, since the
     precision/recall trade-off here is the whole point of the module.
  2. Implementation lookup — hits live Légifrance, so it prints rather
     than asserts (JORF content changes; a hard assertion would rot).

Run: PYTHONPATH=. python test_decree_tracker.py
"""

from legicivica.tools.decree_tracker import (
    extract_decree_deferrals,
    implementation_status,
    law_number_from_title,
    match_implementing_texts,
    merge_implementing_texts,
    summarize_deferrals,
)

# Real fragments from LOI n° 2026-794 (droit à l'aide à mourir), which is
# unusually rich in both true deferrals and the constructions that look
# like one but aren't.
DEFERRALS_EXPECTED = [
    "entre en vigueur à une date fixée par décret, et au plus tard le 31 décembre 2028",
    "Un décret en Conseil d'Etat précise les conditions d'application de la présente sous-section",
    "dans des conditions définies par un décret en Conseil d'Etat pris après avis de la Commission",
    "les modalités sont déterminées par décret en Conseil d'Etat",
    "désignées par arrêté du ministre chargé de la santé",
    "Un arrêté conjoint des ministres chargés de la santé et de la sécurité sociale",
]

# These must NOT count: each points at a text established elsewhere, so
# counting them would double-count a single required décret and overstate
# how much of the law is unimplemented.
NOT_DEFERRALS = [
    "le décret n° 2020-1310 du 29 octobre 2020 est abrogé",
    "l'arrêté du ministre chargé de la santé mentionné au second alinéa du 1°",
    "Ce décret définit notamment les catégories de données",
    "le décret prévu au premier alinéa",
    "dans les conditions fixées par le décret précité",
]


def test_extraction():
    print("--- deferral detection")
    for text in DEFERRALS_EXPECTED:
        found = extract_decree_deferrals(text)
        assert len(found) == 1, f"expected 1 deferral in {text!r}, got {len(found)}"
        print(f"  ok  [{found[0]['kind']}] {found[0]['trigger']!r}")

    print("--- non-deferrals correctly ignored")
    for text in NOT_DEFERRALS:
        found = extract_decree_deferrals(text)
        assert not found, f"expected no deferral in {text!r}, got {found}"
        print(f"  ok  ignored: {text[:58]}...")

    eiv = extract_decree_deferrals(DEFERRALS_EXPECTED[0])
    assert eiv[0]["concerns_entry_into_force"], "entry-into-force deferral not flagged"
    print("  ok  entry-into-force deferral flagged")

    summary = summarize_deferrals(extract_decree_deferrals(" ".join(DEFERRALS_EXPECTED)))
    assert summary["total"] == len(DEFERRALS_EXPECTED), summary
    assert summary["blocks_entry_into_force"] is True
    print(f"  ok  summary: {summary}")


def test_matching():
    print("--- law number parsing")
    assert law_number_from_title("LOI n° 2026-794 du 18 août 2026 relative au ...") == "2026-794"
    assert law_number_from_title("no number here") is None
    print("  ok")

    print("--- citation matching is prefix-safe")
    candidates = [
        {"id": "A", "date": "2026-08-09", "title": "Décret n° 2026-757 pris pour l'application de la loi n° 2025-1403 du 30 décembre 2025"},
        {"id": "B", "date": "2026-08-11", "title": "Décret n° 2026-999 portant nomination d'un préfet"},
    ]
    matched = match_implementing_texts(["2025-1403", "2025-14", "2026-999"], candidates)
    assert len(matched["2025-1403"]) == 1, matched
    # "2025-14" must NOT match "2025-1403" — the bug this guard exists for.
    assert matched["2025-14"] == [], matched
    # A décret must not match on its OWN number, only on a cited loi.
    assert matched["2026-999"] == [], matched
    print("  ok  2025-14 does not match 2025-1403; décret's own number ignored")

    print("--- accumulation is monotonic")
    existing = [{"id": "A", "date": "2026-08-09"}]
    assert [t["id"] for t in merge_implementing_texts(existing, [])] == ["A"]
    merged = merge_implementing_texts(existing, [{"id": "B", "date": "2026-07-01"}])
    assert [t["id"] for t in merged] == ["B", "A"], merged
    print("  ok  nothing is ever dropped; sorted by date")


def test_status():
    print("--- status transitions")
    assert implementation_status([], [])["status"] == "not_required"
    assert implementation_status([{"concerns_entry_into_force": True}], [])["status"] == "blocked"
    assert implementation_status([{"concerns_entry_into_force": False}], [])["status"] == "unknown"
    assert implementation_status([{"concerns_entry_into_force": True}], [{"id": "A"}])["status"] == "implemented"
    print("  ok  not_required / blocked / unknown / implemented")
    print("  note: 'unknown' rather than 'pending' is deliberate — title-only")
    print("        matching cannot see body-only citations, so absence of a")
    print("        match is not evidence the law is unimplemented.")


if __name__ == "__main__":
    test_extraction()
    test_matching()
    test_status()
    print("\nall assertions passed")
