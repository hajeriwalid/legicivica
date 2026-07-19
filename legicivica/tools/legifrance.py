import os
import re
import httpx
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

_SANDBOX = os.getenv("PISTE_SANDBOX", "true").lower() == "true"
_BASE_URL = (
    "https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app"
    if _SANDBOX
    else "https://api.piste.gouv.fr/dila/legifrance/lf-engine-app"
)
_TOKEN_URL = (
    "https://sandbox-oauth.piste.gouv.fr/api/oauth/token"
    if _SANDBOX
    else "https://oauth.piste.gouv.fr/api/oauth/token"
)


def _get_token() -> str:
    """Exchange client credentials for a Bearer token."""
    response = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": os.getenv("PISTE_CLIENT_ID"),
            "client_secret": os.getenv("PISTE_CLIENT_SECRET"),
            "scope": "openid",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}


def _strip_html(html: str) -> str:
    """Remove HTML tags from article content — the API returns HTML."""
    return re.sub(r"<[^>]+>", " ", html or "").strip()


def _ms_to_iso(ms) -> str:
    """Convert a millisecond epoch timestamp (int or str) to ISO date string."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(ms)


def _normalize_article_num(num: str) -> str:
    """
    Normalize a human-readable article number to the format the API expects.

    The API uses 'L541-10-3' not 'L. 541-10-3' — remove dot and spaces after
    the letter prefix.
    """
    # "L. 541-10-3" -> "L541-10-3", "R. 123-4" -> "R123-4"
    return re.sub(r'^([A-Z])\.\s*', r'\1', num.strip())


def fetch_law_text(text_id: str) -> dict:
    """
    Fetch the full text of a JORF law from Légifrance by its text ID.

    Uses /consult/jorf — the correct endpoint for laws published in the
    Journal Officiel (JORFTEXT... identifiers).

    Args:
        text_id: The JORF text identifier, e.g. "JORFTEXT000054399113"

    Returns:
        A dict with keys:
          - id: the text identifier
          - title: official title of the law
          - date: publication date (ISO string from dateTexte)
          - articles: list of article dicts, each with {id, num, content}
          - raw: the full raw API response (for debugging)
    """
    response = httpx.post(
        f"{_BASE_URL}/consult/jorf",
        headers=_headers(),
        json={"textCid": text_id},
    )
    response.raise_for_status()
    data = response.json()

    # Articles appear at top level AND nested inside sections.
    # We collect both so nothing is missed.
    articles = []

    def collect_articles(items: list):
        for item in items:
            if item.get("content") is not None:  # it's an article
                articles.append({
                    "id": item.get("id", ""),
                    "num": item.get("num", ""),
                    "content": _strip_html(item.get("content", "")),
                })
            for sub in item.get("sections", []):
                collect_articles([sub])
            for art in item.get("articles", []):
                articles.append({
                    "id": art.get("id", ""),
                    "num": art.get("num", ""),
                    "content": _strip_html(art.get("content", "")),
                })

    collect_articles(data.get("sections", []))
    for art in data.get("articles", []):
        articles.append({
            "id": art.get("id", ""),
            "num": art.get("num", ""),
            "content": _strip_html(art.get("content", "")),
        })

    return {
        "id": text_id,
        "title": data.get("title", ""),
        "date": _ms_to_iso(data.get("dateTexte", "")),
        "articles": articles,
        "source": "Légifrance / DILA — Etalab Open License 2.0",
        "raw": data,
    }


def fetch_code_article(article_id: str) -> dict:
    """
    Fetch the current text of a specific article from a legal code by its
    Légifrance stable ID (LEGIARTI...).

    Uses /consult/getArticle.
    Response: {"article": {"num": ..., "texteHtml": ..., ...}, ...}

    Args:
        article_id: e.g. "LEGIARTI000006834457"

    Returns:
        A dict with keys:
          - id: the article identifier
          - code: name of the legal code (from article context)
          - num: article number (e.g. "L. 541-10-3")
          - content: plain-text content (HTML stripped)
    """
    response = httpx.post(
        f"{_BASE_URL}/consult/getArticle",
        headers=_headers(),
        json={"id": article_id},
    )
    response.raise_for_status()
    data = response.json()
    article = data.get("article", {})

    return {
        "id": article_id,
        "code": article.get("context", {}).get("titreCode", ""),
        "num": article.get("num", ""),
        "content": _strip_html(article.get("texteHtml", "")),
        "source": "Légifrance / DILA — Etalab Open License 2.0",
    }


def search_code_article(code_name: str, article_num: str) -> dict:
    """
    Find an article by its human-readable reference (code name + article number).

    Uses /search with fond=CODE_ETAT (in-force articles only).

    Args:
        code_name: e.g. "code de l'environnement"
        article_num: e.g. "L. 541-10-3"

    Returns:
        The same structure as fetch_code_article(), or an error dict.
    """
    # The API expects 'L541-10-3' not 'L. 541-10-3'
    normalized_num = _normalize_article_num(article_num)

    response = httpx.post(
        f"{_BASE_URL}/search",
        headers=_headers(),
        json={
            "fond": "CODE_ETAT",
            "recherche": {
                "champs": [
                    {
                        "typeChamp": "NUM_ARTICLE",
                        "criteres": [
                            {
                                "valeur": normalized_num,
                                "typeRecherche": "EXACTE",
                                "operateur": "ET",
                            }
                        ],
                        "operateur": "ET",
                    }
                ],
                "pageNumber": 1,
                "pageSize": 5,
                "operateur": "ET",
                "sort": "PERTINENCE",
                "typePagination": "DEFAUT",
            },
        },
    )
    response.raise_for_status()
    results = response.json().get("results", [])

    if not results:
        return {"error": f"Article {article_num} not found in {code_name}"}

    # The article ID is nested inside sections[].extracts[], not at the top level
    article_id = None
    for section in results[0].get("sections", []):
        for extract in section.get("extracts", []):
            if extract.get("id"):
                article_id = extract["id"]
                break
        if article_id:
            break

    if not article_id:
        return {"error": f"Search returned a result with no extractable ID for {article_num}"}

    return fetch_code_article(article_id)
