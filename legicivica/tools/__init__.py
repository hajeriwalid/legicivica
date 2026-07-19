from .legifrance import fetch_law_text, search_code_article


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
