from .legifrance import fetch_law_text, search_code_article
from .resolver import resolve_law_references


def get_law(text_id: str) -> dict:
    """
    Retrieve the full text of a French law from Légifrance.

    Use this when you have a JORF text identifier like JORFTEXT000054399113.
    Returns the law's title, publication date, and all its articles.

    Args:
        text_id: The JORF text identifier (starts with JORFTEXT)
    """
    return fetch_law_text(text_id)


def get_code_article(code_name: str, article_num: str) -> dict:
    """
    Retrieve the current text of a specific article from a French legal code.

    Use this when a law references another article — for example when it says
    "l'article L. 541-10-3 du code de l'environnement est ainsi modifié".
    This tool fetches what that article currently says, so you can explain
    what the amendment actually changes.

    Args:
        code_name: The name of the legal code, e.g. "code de l'environnement"
        article_num: The article number, e.g. "L. 541-10-3"
    """
    return search_code_article(code_name, article_num)


def get_law_with_references(text_id: str, max_depth: int = 2, max_articles: int = 25) -> dict:
    """
    Retrieve a French law AND recursively resolve every code article it
    references, so it doesn't rely on the model to remember or ask for
    referenced articles one at a time.

    Use this instead of get_law when the user wants to understand what a
    law actually changes, not just its raw text — for example "what does
    this law modify?" or "give me the full picture of this law and
    everything it touches". The result already contains the current text
    of every article the law amends or points to, resolved up to
    max_depth hops of references-within-references.

    Args:
        text_id: The JORF text identifier (starts with JORFTEXT)
        max_depth: How many hops of references to follow beyond the
            law's own articles (default 2). Higher values surface more
            context but cost more API calls.
        max_articles: Hard cap on the number of referenced articles
            fetched, to keep the call bounded (default 25).
    """
    return resolve_law_references(text_id, max_depth=max_depth, max_articles=max_articles)
