from typing import Literal

from pydantic import BaseModel, Field


class ReferenceExplanation(BaseModel):
    """What a law's amendment actually does to one article it references."""

    code: str = Field(description="The legal code the article belongs to, e.g. 'code de l'environnement'.")
    num: str = Field(description="The article number, e.g. 'L. 541-10-3'.")
    in_its_own_words: str = Field(
        description="A short quote or close paraphrase of the article's relevant passage, "
        "taken strictly from the resolved 'before' text given — never from prior knowledge."
    )
    what_changes: str = Field(
        description="What the law's amendment instruction does to this article, stated as a "
        "before/after where the text allows it."
    )
    example: str = Field(
        description="One concrete example of the change in effect — a specific date, amount, "
        "or scenario. Not an abstract restatement of the rule."
    )
    status: Literal["self_executing", "awaiting_decree"] = Field(
        description="'self_executing' if the change applies on its own. 'awaiting_decree' if it "
        "depends on a decret d'application (ministerial decree) that has not been published yet."
    )
    awaiting_detail: str | None = Field(
        default=None,
        description="If status is 'awaiting_decree', what specifically is left undefined — e.g. "
        "which thresholds or dates are not yet set. Never a guessed number. Null if self_executing.",
    )


class LawExplanation(BaseModel):
    """Plain-language explanation of what a law changes, grounded in resolved references."""

    law_id: str
    law_title: str
    summary: str = Field(
        description="A short, plain-language summary of what the law does overall — no legal "
        "jargon, written for someone without a law degree."
    )
    references: list[ReferenceExplanation] = Field(
        description="One entry per resolved reference that the law meaningfully changes. "
        "References that exist only as procedural pointers (no real amendment) can be omitted."
    )


class AffectedParty(BaseModel):
    """One category of person or organization the law's changes apply to."""

    category: str = Field(
        description="Who this applies to, e.g. 'producers', 'online platforms', 'consumers', "
        "'enforcement bodies'. Use whatever categories the explanation actually supports — do "
        "not force a fixed list."
    )
    obligation: str = Field(
        description="What this category is specifically required — or entitled — to do, in "
        "plain language."
    )
    effective_date: str | None = Field(
        default=None,
        description="When this obligation takes effect, if stated. Null if not given, including "
        "when the obligation is still awaiting a decree.",
    )


class ConcretenessAssessment(BaseModel):
    """How concrete the law's own language is, independent of what's deferred to decree."""

    score: int = Field(
        description="1 (vague throughout — 'as appropriate', 'where relevant', no numbers) to "
        "5 (concrete throughout — specific numeric thresholds and dates)."
    )
    reason: str = Field(
        description="One or two sentences citing specific concrete or vague terms found, as "
        "justification for the score."
    )


class ImpactClassification(BaseModel):
    """
    LLM-produced half of the impact report: who's affected, and how concrete the
    law's own language is. The other half — delegation ratio, cross-document
    complexity, and the final combined score — is computed deterministically in
    legicivica.tools.scoring from data these agents already produced, not judged
    by a model.
    """

    law_id: str
    affected_parties: list[AffectedParty]
    concreteness: ConcretenessAssessment


class CivicCriterionScore(BaseModel):
    """
    One criterion's score against a single, narrowly-scoped civic principle.

    verdict is a string enum, not a numeric one — Gemini's structured-output
    schema requires enum values to be strings; a Literal[-1, 0, 1] gets
    rejected by the API as an invalid schema. scoring.py maps the string back
    to -1/0/+1, same pattern as the string status values elsewhere.
    """

    verdict: Literal["weakens", "neutral_or_not_applicable", "reinforces"] = Field(
        description="'weakens' if this law's provisions weaken the principle, 'reinforces' if "
        "they reinforce it, 'neutral_or_not_applicable' if neutral OR if the principle plainly "
        "doesn't apply to this law's subject matter."
    )
    reason: str = Field(
        description="One or two sentences grounded strictly in the law's explained changes — "
        "cite the specific provision behind the verdict. If neutral_or_not_applicable because "
        "the criterion doesn't apply, say so explicitly rather than forcing a judgment the text "
        "doesn't support."
    )


class CivicHealthAssessment(BaseModel):
    """
    A scoped civic/rule-of-law assessment against five criteria that are
    genuinely checkable from a single law's own text — deliberately narrower
    than full frameworks like the Venice Commission's, most of which describe
    properties of an entire political system (judicial independence, electoral
    competition, separation of powers) that one ordinary piece of legislation
    can't meaningfully be scored against. This is normative civic analysis of
    the text's provisions, not a legal or constitutional validity claim, and it
    never names or attributes motive to individual officials — only the
    provisions of the law itself.
    """

    law_id: str
    legal_certainty: CivicCriterionScore = Field(
        description="Are obligations, thresholds, and effective dates stated clearly and "
        "prospectively — not retroactively — so those affected can know them in advance?"
    )
    proportionality: CivicCriterionScore = Field(
        description="Are the obligations and penalties reasonably calibrated to the law's stated "
        "goal, rather than open-ended or disproportionate in scope?"
    )
    sunset_and_review: CivicCriterionScore = Field(
        description="Does the law include an expiration date, or mandate a review report (e.g. a "
        "report to Parliament) that revisits its effects?"
    )
    subsidiarity: CivicCriterionScore = Field(
        description="Does implementation respect local or regional decision-making where "
        "relevant, rather than centralizing all authority at the national or ministerial level?"
    )
    open_government: CivicCriterionScore = Field(
        description="Does the law create or expand public-facing disclosure obligations (e.g. "
        "labeling, public reporting), rather than restricting access to information?"
    )
