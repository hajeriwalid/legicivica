import re

# Codes de référence les plus fréquemment amendés ou cités par renvoi.
# Not exhaustive — Légifrance lists ~80 codes. Extend as new ones show up
# in the wild; an unrecognized code name is simply not captured (a
# reference is dropped rather than guessed).
KNOWN_CODES = [
    "code civil",
    "code pénal",
    "code de procédure pénale",
    "code de procédure civile",
    "code du travail",
    "code de la sécurité sociale",
    "code de la santé publique",
    "code de l'environnement",
    "code de la consommation",
    "code de commerce",
    "code monétaire et financier",
    "code général des impôts",
    "code de l'éducation",
    "code de l'urbanisme",
    "code rural et de la pêche maritime",
    "code des transports",
    "code de la propriété intellectuelle",
    "code des assurances",
    "code de la construction et de l'habitation",
    "code électoral",
    "code des relations entre le public et l'administration",
    "code de la route",
    "code de justice administrative",
    "code général des collectivités territoriales",
    "code de la défense",
    "code de l'action sociale et des familles",
    "code de l'énergie",
    "code du patrimoine",
    "code du sport",
    "code forestier",
    "code minier",
    "code de la mutualité",
    "code des douanes",
    "code de la copropriété",
]

# Longest name first, so "code rural et de la pêche maritime" wins over a
# shorter alternative that happens to be a prefix of it.
_CODE_ALT = "|".join(re.escape(c) for c in sorted(KNOWN_CODES, key=len, reverse=True))
_CODE_RE = rf"(?:{_CODE_ALT})"
_SAME_CODE_RE = r"(?:m[êe]me\s+code|dudit\s+code)"

# An article number is either letter-prefixed (L./R./D./A., the standard
# "codified" form: L. 541-10-3) or a bare number (238 bis, 11) — the form
# used by non-codified texts like the Code général des impôts or ordonnances.
_NUM_RE = (
    r"[LRDA]\.?\s?\d+(?:-\d+)*(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies))?"
    r"|\d+(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies))?"
)
_NUM_LIST_RE = rf"(?:{_NUM_RE})(?:\s*(?:,|et|ou|à)\s*(?:{_NUM_RE}))*"

# Single pass over the text: either an "article(s) ... du code de X" (or
# "... du même code") reference, or a bare mention of a known code name
# used to update which code subsequent bare references belong to.
_SCAN_RE = re.compile(
    rf"""
    (?P<article_ref>
        articles?\s+(?P<nums>{_NUM_LIST_RE})
        (?:\s+(?:du|de\s+la|de\s+l['’]|des)\s+(?P<code>{_CODE_RE})
           |\s+(?:du|de\s+la)\s+(?P<same_code>{_SAME_CODE_RE})
        )?
    )
    |
    (?P<code_mention>{_CODE_RE})
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SPLIT_NUM_RE = re.compile(rf"({_NUM_RE})")


def _normalize_num(num: str) -> str:
    """"l541-10-3" / "l. 541-10-3" / "L541-10-3" -> "L. 541-10-3"."""
    num = re.sub(r"\s+", " ", num.strip())
    num = re.sub(r"^([A-Za-z])\.?\s*", lambda m: m.group(1).upper() + ". ", num)
    return num


def extract_references(text: str, default_code: str | None = None) -> list[dict]:
    """
    Scan a chunk of French legal text and extract the code article
    references it contains.

    Returns a list of {"code": str, "num": str, "explicit": bool} dicts,
    one per distinct reference, in the order they first appear.

    How code names are resolved for a reference:
      - Explicit ("explicit": True): "l'article L. 541-10-3 du code de
        l'environnement" -> code taken directly from the match. This is
        the only case where the code name was actually stated next to
        this specific reference, as opposed to inferred.
      - Carried forward ("explicit": False): "l'article L. 541-9-7" (no
        code attached), or "l'article L. 541-10-1 du même code" /
        "dudit code" -> resolved against the most recently mentioned
        code earlier in the same text (this covers the common drafting
        pattern "Le code de X est ainsi modifié : 1° ... 2° ..." where
        the code is stated once and every following article number is
        implicitly in that code), or against `default_code` if nothing
        has been mentioned yet. The `explicit` flag lets a caller that
        sees the same article number elsewhere with an explicit code
        attached prefer that higher-confidence source instead — see
        `resolver.py`'s cross-article correction.

    Known limitations (by design, not bugs):
      - A bare numeric reference with no letter prefix ("l'article 3")
        and no explicit code attached is dropped rather than guessed —
        in practice these are almost always a paragraph of an EU
        directive or another law being cited in passing (e.g. "l'article
        3 de la directive 2000/31/CE"), not a code article, and blindly
        carrying the code forward produces confident-looking garbage.
      - "du même code" inside a quoted/inserted provision refers to
        whatever code that provision's own quoted context establishes,
        which is not always the same as the last code named in the
        surrounding (unquoted) text. This parser always uses the latter,
        so taken in isolation it can misattribute a reference. The
        resolver mitigates this — see `resolve_law_references` — by
        cross-checking non-explicit references against every explicit
        mention of the same article number elsewhere in the same law.
        When no such explicit mention exists anywhere else, the
        misattribution can still slip through; when it does, the fetch
        for that article usually comes back as a clean "not found"
        rather than silently wrong content.
      - References to other laws ("loi n° 2023-451 du 9 juin 2023") and
        EU directives ("directive 2010/13/UE") are not extracted at
        all — only Légifrance code articles are, since those are what
        the resolver can actually fetch.
    """
    text = re.sub(r"\s+", " ", text)
    current_code = default_code
    refs = []
    seen = set()

    for m in _SCAN_RE.finditer(text):
        if m.group("code_mention") and not m.group("article_ref"):
            current_code = m.group("code_mention").lower()
            continue

        nums_raw = m.group("nums")
        code = m.group("code")
        same_code = m.group("same_code")

        if code:
            current_code = code.lower()
        ref_code = current_code
        explicit = bool(code)

        if not ref_code:
            continue

        for num_match in _SPLIT_NUM_RE.finditer(nums_raw):
            num_raw = num_match.group(1)
            has_letter_prefix = bool(re.match(r"[A-Za-z]", num_raw))
            if not has_letter_prefix and not (code or same_code):
                continue
            num = _normalize_num(num_raw)
            key = (ref_code, num)
            if key in seen:
                continue
            seen.add(key)
            refs.append({"code": ref_code, "num": num, "explicit": explicit})

    return refs
