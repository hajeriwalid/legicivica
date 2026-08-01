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
