from google.adk.agents import LlmAgent
from legicivica.agents.schemas import LawExplanation
from legicivica.tools import get_law, get_code_article, get_law_with_references

law_fetcher = LlmAgent(
    name="law_fetcher",
    model="gemini-flash-latest",
    tools=[get_law, get_code_article, get_law_with_references],
    instruction="""
    You are a legal document retrieval assistant. Your job is to fetch French laws
    and return their content accurately. Do not summarize or interpret the law —
    just retrieve it and return the raw content.

    When given a JORF text ID and the user just wants that law's own text, use
    the get_law tool.
    When given a reference like "article L. 541-10-3 du code de l'environnement",
    use the get_code_article tool with the code name and article number.
    When the user wants to understand what a law actually changes — its full
    context, everything it references, or "the complete picture" — use
    get_law_with_references instead of get_law. It returns the law plus the
    current text of every article it points to, already resolved.

    Always return the full article content, not a summary.
    """,
)

explainer_agent = LlmAgent(
    name="explainer",
    model="gemini-pro-latest",
    output_schema=LawExplanation,
    instruction="""
    You are a legal explainer for French law. You will be given, as input:
      1. The root law's own text — its amendment instructions.
      2. For every reference the law makes that was successfully resolved,
         the CURRENT text of that article — its "before" state.

    Ground every explanation strictly in the text you were given. Never rely
    on your own prior knowledge of French law — it may be stale, or simply
    wrong for a law published very recently, and the "before" text you were
    given is the only thing guaranteed to be current.

    For each resolved reference that the law meaningfully changes:
      - Quote or closely paraphrase the article's relevant passage, from the
        text you were given, in in_its_own_words.
      - Explain what the amendment does to it in what_changes, as a
        before/after where the text allows it.
      - Give one concrete example of the change in effect in example — a
        specific date, amount, or scenario. Not an abstract restatement of
        the rule.
      - Set status to "self_executing" if the change applies on its own, or
        "awaiting_decree" if it depends on a "decret d'application"
        (ministerial decree) that has not been published yet. If
        awaiting_decree, state in awaiting_detail exactly what is left
        undefined — never guess a threshold, amount, or date the text
        doesn't give you.

    Omit references that are purely procedural pointers with no real
    amendment of their own. Write summary and every field in plain language,
    for a reader without a law degree.
    """,
)


def build_explainer_prompt(resolver_result: dict) -> str:
    """
    Turn a resolve_law_references() result into the input text the
    explainer agent will reasons ove: the law's own text, plus the current text
    of every reference that was actually resolved.

    References the resolver found but could not fetch are deliberately left
    out of this prompt
    Use list_unresolved_references() to get that list directly from the
    resolver's own output instead, and merge it into the explanation
    yourself.
    """
    root = resolver_result["root"]
    lines = [
        f"LAW: {root['title']}",
        f"ID: {root['id']}",
        f"PUBLISHED: {root['date']}",
        "",
        "=== The law's own text (amendment instructions) ===",
    ]
    for article in root["articles"]:
        lines.append(f"--- Article {article['num']} ---")
        lines.append(article["content"])
        lines.append("")

    lines.append('=== Referenced articles, current text ("before" state) ===')
    for ref in resolver_result["resolved"]:
        lines.append(f"--- {ref['code']} — art. {ref['num']} ---")
        lines.append(ref["content"])
        lines.append("")

    return "\n".join(lines)


def list_unresolved_references(resolver_result: dict) -> list[dict]:
    """
    Build the "couldn't explain this, here's why" list directly from the
    resolver's own output — depth/budget skips and fetch errors alike —
    instead of asking the explainer agent to transcribe it.
    """
    unresolved = []
    for ref in resolver_result["skipped_max_depth"]:
        unresolved.append({"code": ref["code"], "num": ref["num"], "reason": "max_depth"})
    for ref in resolver_result["skipped_max_articles"]:
        unresolved.append({"code": ref["code"], "num": ref["num"], "reason": "max_articles"})
    for ref in resolver_result["errors"]:
        unresolved.append({"code": ref["code"], "num": ref["num"], "reason": "error"})
    return unresolved
