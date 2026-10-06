"""
Detect where a law defers substance to a future implementing text
(décret / arrêté), and find whether that text was ever actually published.

Why this exists: a French law routinely leaves the operative detail to a
décret d'application — "les modalités sont fixées par décret". Until that
décret appears, the provision is, in practice, not in force. A reader of
the law alone cannot tell whether it took effect. This module closes that
loop: `extract_decree_deferrals` finds the promises, and
`find_implementing_texts` looks for texts that cite the law back.

No ADK dependency — testable standalone, like legifrance.py.
"""

import re

from legicivica.tools.legifrance import search_jorf_texts

# --- Deferral detection ----------------------------------------------------
#
# The hard part is not finding the word "décret" — it is telling a *promise
# of a future text* apart from a *reference to one that already exists*.
# Both appear constantly in the same law. Verified against real JORF text:
#
#   deferral      "les modalités sont fixées par décret en Conseil d'Etat"
#   deferral      "Un décret en Conseil d'Etat précise les conditions"
#   deferral      "désignées par arrêté du ministre chargé de la santé"
#   NOT deferral  "le décret n° 2020-1310 du 29 octobre 2020"   (numbered)
#   NOT deferral  "l'arrêté ... mentionné au second alinéa"     (cross-ref)
#   NOT deferral  "Ce décret définit notamment ..."             (anaphoric)
#
# The last two matter for counting: they point back at a text established
# elsewhere in the same law, so treating them as new deferrals would
# double-count a single required décret.

_KIND_RE = r"""
    (?P<kind>
        d[ée]cret\s+en\s+Conseil\s+d['’]?\s*[EÉ]tat
      | d[ée]cret\s+en\s+conseil\s+des\s+ministres
      | d[ée]crets?
      | arr[êe]t[ée]s?\s+conjoints?
      | arr[êe]t[ée]s?
    )
"""

# Anything in this set immediately after the noun means the text is being
# *referred to*, not promised: a number, or a back-reference participle.
_EXISTING_RE = r"""
    (?!\s*(?:
        n\s*[°ºo]
      | mentionn[ée]
      | pr[ée]vu
      | pr[ée]cit[ée]
      | susvis[ée]
      | susmentionn[ée]
      | du\s+\d{1,2}\s+\w+\s+\d{4}
    ))
"""

_DEFERRAL_RE = re.compile(
    rf"""
    (?<![\w'’])
    (?:
        par\s+(?:un\s+|une\s+|le\s+|la\s+|les\s+)?     # "fixées par décret"
      | (?:un|une)\s+                                  # "Un décret précise"
    )
    {_KIND_RE}
    {_EXISTING_RE}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A deferral attached to entry-into-force is the strongest possible signal:
# the provision cannot apply at all until the text lands.
_IN_FORCE_RE = re.compile(
    r"entre(?:nt)?\s+en\s+vigueur|entr[ée]e\s+en\s+vigueur|date\s+fix[ée]e",
    re.IGNORECASE,
)

_CTX = 130


def _canonical_kind(raw: str) -> str:
    """Collapse spelling/plural/accent variants onto one label."""
    k = re.sub(r"\s+", " ", raw.strip().lower())
    if "conseil d" in k and "état" in k.replace("etat", "état"):
        return "décret en Conseil d'État"
    if "conseil des ministres" in k:
        return "décret en conseil des ministres"
    if k.startswith("décret") or k.startswith("decret"):
        return "décret"
    if "conjoint" in k:
        return "arrêté conjoint"
    return "arrêté"


def extract_decree_deferrals(text: str) -> list[dict]:
    """
    Find every point where `text` defers substance to a future décret/arrêté.

    Returns a list of dicts, in order of first appearance:
        {
          "kind": "décret en Conseil d'État" | "décret" | "arrêté" | ...,
          "trigger": the matched phrase, verbatim,
          "context": ~260 chars of surrounding text (for display/audit),
          "concerns_entry_into_force": bool,
        }

    Precision is favoured over recall: a construction that cannot be told
    apart from a reference to an existing text is dropped rather than
    guessed, on the same principle as reference_parser.py. Counting a
    deferral that isn't there would overstate how much of a law is
    unimplemented, which is the more damaging error for this project.
    """
    flat = re.sub(r"\s+", " ", text or "")
    out = []
    seen_spans = []

    for m in _DEFERRAL_RE.finditer(flat):
        # Overlapping matches (e.g. "décret" inside "décret en Conseil
        # d'État") would inflate the count — keep only the first/longest.
        if any(m.start() < end and start < m.end() for start, end in seen_spans):
            continue
        seen_spans.append((m.start(), m.end()))

        lo = max(0, m.start() - _CTX)
        hi = min(len(flat), m.end() + _CTX)
        context = flat[lo:hi].strip()

        out.append({
            "kind": _canonical_kind(m.group("kind")),
            "trigger": m.group(0).strip(),
            "context": context,
            "concerns_entry_into_force": bool(_IN_FORCE_RE.search(context)),
        })

    return out


def summarize_deferrals(deferrals: list[dict]) -> dict:
    """Aggregate counts, shaped for storage on a law record."""
    by_kind: dict[str, int] = {}
    for d in deferrals:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
    return {
        "total": len(deferrals),
        "by_kind": by_kind,
        "blocks_entry_into_force": any(d["concerns_entry_into_force"] for d in deferrals),
    }


# --- Implementation lookup -------------------------------------------------
#
# Given a law, has any implementing text actually been published?
#
# IMPORTANT — what this can and cannot see. Légifrance's /search full-text
# field is broken (see search_jorf_texts' docstring), and the curated
# `liens` array comes back empty on both /consult/jorf and /consult/legiPart
# for every law tested. So there is no way to ask "which décrets implement
# law X". What remains is: enumerate décrets by date, and match those whose
# *title* cites the law number — e.g. "Décret n° 2026-757 du 8 août 2026
# pris pour l'application des articles 10 et 18 de la loi n° 2025-14...".
#
# That is sound but incomplete: measured against a real month of JORF, only
# 1 décret in 100 names a law in its title. Many implementing décrets cite
# the law only in their body (the "visa"). So:
#
#   a match here is strong evidence the law WAS implemented;
#   no match is NOT evidence it wasn't.
#
# Every function below preserves that asymmetry, and `implementation_status`
# returns "unknown" rather than "pending" when it cannot tell. Overstating
# how many laws are unimplemented would be the damaging error for this
# project — it is the exact claim a journalist would repeat.

_LAW_NUM_RE = re.compile(r"n[°ºo]\s*(\d{4}-\d+)", re.IGNORECASE)


def law_number_from_title(title: str) -> str | None:
    """
    "LOI n° 2026-794 du 18 août 2026 relative au ..." -> "2026-794".

    Implementing texts cite the law by this number, so it is the join key.
    """
    m = _LAW_NUM_RE.search(title or "")
    return m.group(1) if m else None


def _cites_law(text_title: str, law_number: str) -> bool:
    """True if `text_title` cites `law_number` as a law (not merely as its own number)."""
    return bool(
        re.search(
            rf"loi\s+(?:organique\s+)?n[°ºo]\s*{re.escape(law_number)}(?!\d)",
            text_title or "",
            re.IGNORECASE,
        )
    )


def match_implementing_texts(law_numbers: list[str], candidates: list[dict]) -> dict[str, list[dict]]:
    """
    Match enumerated JORF texts against many law numbers in one pass.

    Batch by design: sweeping décrets is the expensive part (one API call per
    100 texts), so the poller should enumerate the window ONCE and reconcile
    every tracked law against it, rather than re-sweeping per law.

    Args:
        law_numbers: e.g. ["2026-794", "2025-14"].
        candidates: output of search_jorf_texts().

    Returns:
        {law_number: [matching text dicts]}, every requested number present
        (empty list when nothing matched).
    """
    out: dict[str, list[dict]] = {n: [] for n in law_numbers}
    for text in candidates:
        title = text.get("title", "")
        for number in law_numbers:
            if _cites_law(title, number):
                out[number].append(text)
    return out


def implementation_status(deferrals: list[dict], implementing_texts: list[dict]) -> dict:
    """
    Combine "what did the law promise" with "what has since been published".

    Status values, and why there are four:
      - "not_required"  : the law defers nothing; nothing is awaited.
      - "implemented"   : at least one text citing this law was found.
      - "unknown"       : the law defers, nothing was found, but title
                          matching cannot see body-only citations — so we
                          decline to claim it is unimplemented.
      - "blocked"       : the law defers *its own entry into force* to a
                          future text and none was found. This is the one
                          case worth surfacing loudly: the law cannot apply.

    Note there is deliberately no "pending" — the data cannot support it.
    """
    if not deferrals:
        return {
            "status": "not_required",
            "deferral_count": 0,
            "implementing_text_count": len(implementing_texts),
        }

    blocks = any(d.get("concerns_entry_into_force") for d in deferrals)

    if implementing_texts:
        status = "implemented"
    elif blocks:
        status = "blocked"
    else:
        status = "unknown"

    return {
        "status": status,
        "deferral_count": len(deferrals),
        "implementing_text_count": len(implementing_texts),
        "blocks_entry_into_force": blocks,
    }


# --- Sweeping and accumulation ---------------------------------------------
#
# The JORF search paginates unreliably. Verified 2026-09-03 on production:
# the same query for DECRET over 2026-08-01..2026-09-03 returned 273, 327
# and 285 rows on three consecutive runs, and asking for MORE pages
# sometimes returned FEWER rows. Month-chunking reduces but does not remove
# it (July: 277 then 242 on a repeat).
#
# Consequence: a single sweep can miss an implementing text that genuinely
# exists. So results are ACCUMULATED, never recomputed — `merge_implementing_texts`
# is monotonic, and a text once found is never dropped. Because the poller
# runs weekly, transient misses self-heal on a later pass, and coverage only
# improves. Do not "simplify" this into an overwrite.


def month_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    """Split [start, end] into calendar-month windows, to keep pagination shallow."""
    from datetime import date, timedelta

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    windows = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            nxt = date(cursor.year + 1, 1, 1)
        else:
            nxt = date(cursor.year, cursor.month + 1, 1)
        windows.append((cursor.isoformat(), min(nxt - timedelta(days=1), end).isoformat()))
        cursor = nxt
    return windows


def sweep_jorf_texts(
    start_date: str,
    end_date: str,
    natures: tuple[str, ...] = ("DECRET", "ARRETE"),
) -> list[dict]:
    """
    Enumerate candidate implementing texts across a date range, month by month.

    Returns a de-duplicated list. Failures on an individual window are
    swallowed and logged by the caller's expectations — one bad month must
    not abort a whole reconciliation run.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for nature in natures:
        for win_start, win_end in month_windows(start_date, end_date):
            try:
                rows = search_jorf_texts(win_start, win_end, nature=nature, page_size=100, max_pages=8)
            except Exception:
                continue
            for row in rows:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    out.append(row)
    return out


def merge_implementing_texts(existing: list[dict] | None, found: list[dict]) -> list[dict]:
    """
    Union previously-known implementing texts with newly-found ones.

    Monotonic by design — see the note above on unstable pagination. Sorted
    by date so the earliest implementing text reads first.
    """
    merged: dict[str, dict] = {t["id"]: t for t in (existing or []) if t.get("id")}
    for text in found:
        if text.get("id"):
            merged.setdefault(text["id"], text)
    return sorted(merged.values(), key=lambda t: (t.get("date") or "", t.get("id") or ""))
