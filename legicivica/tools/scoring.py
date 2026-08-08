import re

# Matches both citation forms found in real Légifrance text:
#   "directive 2000/31/CE"        — number, then CE/UE suffix
#   "directive (UE) 2025/1892"    — CE/UE prefix, then number, no suffix
_EU_DIRECTIVE_RE = re.compile(
    r"directive\s+(?:\((?:UE|CE)\)\s*)?\d{4}/\d+(?:/(?:UE|CE))?",
    re.IGNORECASE,
)


def detect_eu_directives(root: dict) -> list[str]:
    """
    Scan a law's own text for EU directive citations, in the order they first
    appear, deduplicated.

    Detection only — this does not fetch the directive's text or compare it to
    the French provision. Prior articles' reference_parser deliberately never
    follows these (Légifrance's CODE_ETAT search can't fetch them the way it
    fetches code articles); this is the same non-following stance, just made
    visible in the transparency report instead of silently dropped, so a
    reader knows which EU-derived obligations exist without LegiCivica having
    verified how closely they track the directive yet.
    """
    found = []
    seen = set()
    for article in root["articles"]:
        for m in _EU_DIRECTIVE_RE.finditer(article["content"]):
            text = re.sub(r"\s+", " ", m.group()).strip()
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(text)
    return found


def score_delegation_ratio(explanation) -> dict:
    """
    How much of what the explainer actually explained is self-executing today
    versus waiting on a decree that doesn't exist yet — a plain count over the
    explainer's own `status` field, not a new judgment call.
    """
    total = len(explanation.references)
    if total == 0:
        return {
            "label": "Delegation ratio",
            "score": 5,
            "reason": "No substantive amendments were explained for this law.",
        }

    awaiting = [r for r in explanation.references if r.status == "awaiting_decree"]
    ratio = len(awaiting) / total

    if ratio == 0:
        score = 5
    elif ratio <= 0.25:
        score = 4
    elif ratio <= 0.5:
        score = 3
    elif ratio <= 0.75:
        score = 2
    else:
        score = 1

    if not awaiting:
        reason = f"All {total} explained change(s) are self-executing today."
    else:
        listed = ", ".join(f"{r.code} art. {r.num}" for r in awaiting)
        reason = (
            f"{len(awaiting)} of {total} explained change(s) wait on a decree "
            f"before their substance is knowable: {listed}."
        )

    return {"label": "Delegation ratio", "score": score, "reason": reason}


def score_cross_document_complexity(resolver_result: dict) -> dict:
    """
    How many distinct legal codes a law touches, and how much of the
    reference chain the resolver couldn't finish following — both read
    directly off the resolver's own output, no LLM involved.
    """
    resolved = resolver_result["resolved"]
    codes = sorted({r["code"] for r in resolved})
    n_codes = len(codes)
    skipped = len(resolver_result["skipped_max_depth"]) + len(resolver_result["skipped_max_articles"])

    if n_codes <= 1:
        score = 5
    elif n_codes == 2:
        score = 4
    elif n_codes == 3:
        score = 3
    elif n_codes <= 5:
        score = 2
    else:
        score = 1

    reason = f"Touches {n_codes} distinct code(s)" + (f" ({', '.join(codes)})" if codes else "") + "."
    if skipped:
        reason += (
            f" {skipped} further reference(s) exist beyond the resolver's depth/budget "
            "limit and were never read, so the true complexity may be higher."
        )

    return {"label": "Cross-document complexity", "score": score, "reason": reason}


def build_transparency_report(explanation, resolver_result: dict, classification) -> dict:
    """
    Combine the two deterministic components (delegation ratio,
    cross-document complexity) with the one LLM-judged component
    (concreteness of definitions) into a single, inspectable report.

    The overall score is a plain average of the three component scores —
    not a weighted formula. Deliberately: with one real law tested so far,
    picking weights would be guessing at calibration this project doesn't
    have evidence for yet. EU directive citations are listed for visibility,
    not scored — see detect_eu_directives.
    """
    delegation = score_delegation_ratio(explanation)
    complexity = score_cross_document_complexity(resolver_result)
    concreteness = {
        "label": "Concreteness of definitions",
        "score": classification.concreteness.score,
        "reason": classification.concreteness.reason,
    }
    components = [delegation, complexity, concreteness]
    overall = round(sum(c["score"] for c in components) / len(components), 1)

    return {
        "law_id": resolver_result["root"]["id"],
        "law_title": resolver_result["root"]["title"],
        "affected_parties": classification.affected_parties,
        "eu_directives_referenced": detect_eu_directives(resolver_result["root"]),
        "components": components,
        "overall_score": overall,
        "overall_max": 5,
    }


_CIVIC_CRITERIA = [
    ("legal_certainty", "Legal certainty & prospectivity"),
    ("proportionality", "Proportionality of obligations"),
    ("sunset_and_review", "Sunset & review mechanisms"),
    ("subsidiarity", "Subsidiarity / local autonomy"),
    ("open_government", "Open government & disclosure"),
]

# The schema's verdict field is a string enum, not a numeric one — Gemini's
# structured-output schema rejects a Literal[-1, 0, 1] as invalid (enum
# values must be strings). This maps the model's string verdict back to the
# -1/0/+1 the civic index is actually computed from.
_VERDICT_SCORE = {"weakens": -1, "neutral_or_not_applicable": 0, "reinforces": 1}


def build_civic_report(civic_assessment) -> dict:
    """
    Turn a CivicHealthAssessment into a flat, printable report.

    Deliberately kept separate from build_transparency_report rather than
    folded into one combined number: the transparency score measures
    structural facts about how the law was drafted (delegated to decree?
    how many codes? how concrete?); this one is a normative civic judgment
    about the law's provisions against rule-of-law principles. Mixing the
    two into a single figure would hide which kind of claim is being made.

    A criterion scored 0 because it doesn't apply to this law's subject
    matter and a criterion scored 0 because it's genuinely neutral look
    identical in the total — that ambiguity is why every criterion prints
    its own reason string rather than just contributing to the sum.
    """
    criteria = []
    for field_name, label in _CIVIC_CRITERIA:
        result = getattr(civic_assessment, field_name)
        score = _VERDICT_SCORE[result.verdict]
        criteria.append({"label": label, "score": score, "verdict": result.verdict, "reason": result.reason})

    civic_index = sum(c["score"] for c in criteria)

    return {
        "law_id": civic_assessment.law_id,
        "criteria": criteria,
        "civic_index": civic_index,
        "civic_index_range": f"-{len(criteria)} to +{len(criteria)}",
        "notice": (
            "This is an automated, scoped civic analysis of the law's own text against five "
            "rule-of-law principles. It is not legal advice, not a determination of legal or "
            "constitutional validity, and does not evaluate any individual official."
        ),
    }
