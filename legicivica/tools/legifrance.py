import os
import random
import re
import time

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


# Retry/backoff tuning, shared by the token fetch and every API call.
_MAX_ATTEMPTS = 5
_BACKOFF_BASE = 0.6
_TIMEOUT = 30.0

# Cached bearer token: (token, expiry as a UTC epoch float).
_TOKEN_CACHE: tuple[str, float] | None = None

# Refresh this many seconds before the token actually expires, so a long
# resolver run can't have a token die mid-flight.
_TOKEN_REFRESH_MARGIN = 60.0


def _get_token(force_refresh: bool = False) -> str:
    """
    Return a Bearer token, reusing the cached one until it nears expiry.

    This was previously uncached — a fresh OAuth round trip before *every*
    single API call. That was documented as harmless at low volume, and it
    wasn't: on 2026-10-06, resolving LOI n° 2026-794 produced 13 failures out
    of 28 lookups, all of them `400 Bad Request` from the token endpoint and
    `401 Unauthorized` from the API. PISTE throttles the token endpoint, and
    the resolver's burst of lookups walked straight into it.

    The visible damage was not an error — the resolver swallows per-reference
    failures by design — but silently degraded output: explanations built on a
    third of a law's references, with nothing saying so. The coverage fields
    in agents/provenance.py now make that measurable, and this makes it rare.

    Args:
        force_refresh: skip the cache. Used by the 401 retry in _headers().
    """
    global _TOKEN_CACHE

    now = datetime.now(timezone.utc).timestamp()
    if not force_refresh and _TOKEN_CACHE is not None:
        token, expires_at = _TOKEN_CACHE
        if now < expires_at - _TOKEN_REFRESH_MARGIN:
            return token

    # The token endpoint throttles too, and signals it as 400 rather than 429
    # (observed 2026-10-06 while load-testing). Caching makes this rare — one
    # fetch an hour in normal operation — but a transient failure here breaks
    # every subsequent call, so it gets its own small retry.
    payload = None
    delay = _BACKOFF_BASE
    for attempt in range(1, 4):
        try:
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": os.getenv("PISTE_CLIENT_ID"),
                    "client_secret": os.getenv("PISTE_CLIENT_SECRET"),
                    "scope": "openid",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
            if attempt == 3:
                raise
            time.sleep(delay + random.uniform(0, delay * 0.3))
            delay *= 2
    token = payload["access_token"]

    # PISTE returns expires_in (seconds). Treat a missing/garbled value as a
    # short life rather than assuming a long one — re-fetching early is
    # cheap, serving an expired token is not.
    try:
        lifetime = float(payload.get("expires_in", 0)) or 300.0
    except (TypeError, ValueError):
        lifetime = 300.0

    _TOKEN_CACHE = (token, now + lifetime)
    return token


def _reset_token_cache() -> None:
    """Drop the cached token. For tests, and after an auth failure."""
    global _TOKEN_CACHE
    _TOKEN_CACHE = None


def _headers(force_refresh: bool = False) -> dict:
    return {"Authorization": f"Bearer {_get_token(force_refresh=force_refresh)}"}


# PISTE production rejects a *valid* bearer token intermittently under
# sustained request rates, and reports it as a bare 401 — empty body, no
# Retry-After, no X-RateLimit-* headers. Measured 2026-10-06 against
# production: 40 rapid sequential /consult/getArticle calls using ONE cached
# token returned 36x 200 and 4x 401, first failure at request #24, with that
# same token succeeding both before and after. DILA documents per-token
# per-second quotas; the gateway simply signals them as 401 instead of 429.
#
# 403 is deliberately NOT retried: that is the Légifrance CGU-not-accepted
# case, which is a permanent configuration error. Retrying it would waste
# time and bury the one error whose message actually tells you what to fix.
_RETRYABLE_STATUS = {401, 429, 500, 502, 503, 504}

def _post(path: str, payload: dict) -> httpx.Response:
    """
    POST to the Légifrance API with retry and exponential backoff.

    Every call site goes through here. Before this existed, a sporadic 401
    surfaced as a per-reference resolver failure, which the resolver swallows
    by design — so laws were quietly explained from a fraction of their
    references. Raising max_articles 15 -> 40 tripled the burst per law and
    made it unmissable: 16 of 47 lookups failed on one law.

    Retry strategy, in order:
      - attempts 1-2 reuse the cached token, since the token is valid and the
        rejection is transient; a plain retry after a short backoff is what
        actually fixes it;
      - attempt 3+ forces a token refresh as well, to cover the genuine
        expiry/invalidation case that looks identical from here;
      - backoff is exponential with jitter, so a burst of concurrent failures
        doesn't resynchronise into another burst.

    Raises the underlying httpx error once attempts are exhausted, so callers
    (and the resolver's per-reference error capture) behave as before.
    """
    delay = _BACKOFF_BASE
    last_error: Exception | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                f"{_BASE_URL}{path}",
                headers=_headers(force_refresh=attempt >= 3),
                json=payload,
                timeout=_TIMEOUT,
            )
            if response.status_code == 200:
                return response
            if response.status_code not in _RETRYABLE_STATUS or attempt == _MAX_ATTEMPTS:
                response.raise_for_status()
                return response
            last_error = httpx.HTTPStatusError(
                f"{response.status_code} from {path}", request=response.request, response=response
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt == _MAX_ATTEMPTS:
                raise
            last_error = exc

        time.sleep(delay + random.uniform(0, delay * 0.3))
        delay *= 2

    if last_error:
        raise last_error
    raise RuntimeError(f"unreachable retry state for {path}")


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
    response = _post("/consult/jorf", {"textCid": text_id})
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
    response = _post("/consult/getArticle", {"id": article_id})
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

    response = _post("/search", {
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
        })
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


def search_jorf_by_date_range(start_date: str, end_date: str, nature: str = "LOI", page_size: int = 50) -> list[dict]:
    """
    Search the Journal Officiel for texts published in [start_date, end_date],
    restricted to a given document nature (default "LOI" — actual laws voted
    by Parliament, not décrets, arrêtés, or ordonnances, which would number
    in the hundreds over the same window).

    Uses /search with fond=JORF and a filtres/facette structure (NATURE,
    DATE_PUBLICATION) alongside the champs/typeChamp structure already used
    by search_code_article — this is how discovery works, as opposed to
    fetch_law_text, which requires already knowing a specific JORFTEXT id.

    Args:
        start_date: ISO date string, e.g. "2026-06-01"
        end_date: ISO date string, e.g. "2026-08-01"
        nature: document nature filter, e.g. "LOI"
        page_size: max results per page (Légifrance search is paginated;
            callers processing a bounded window/cap don't need to paginate
            beyond one page in practice)

    Returns:
        A list of dicts, each with {id, title, date, nature} — the JORFTEXT
        id plus enough metadata to decide whether to process it further.
        Never raises on an empty result set — returns [] instead.

    Response shape (verified against a live sandbox call — the JORFTEXT id
    and title are NOT top-level fields, they're nested one level down):
        {
          "nature": "LOI",                         # top-level, matches the filter
          "datePublication": "2026-06-05T00:00:00.000+0000",  # already ISO, not an epoch
          "titles": [{"cid": "JORFTEXT0...", "title": "LOI n° ..."}],
        }
    "cid" is the clean JORFTEXT id — the sibling "id" field carries a
    version-dated suffix (e.g. "..._01-01-2999") that fetch_law_text's
    {"textCid": ...} request body does not expect.
    """
    response = _post("/search", {
            "fond": "JORF",
            "recherche": {
                "champs": [],
                "filtres": [
                    {"facette": "NATURE", "valeurs": [nature]},
                    {"facette": "DATE_PUBLICATION", "dates": {"start": start_date, "end": end_date}},
                ],
                "pageNumber": 1,
                "pageSize": page_size,
                "operateur": "ET",
                "sort": "DATE_ASC",
                "typePagination": "DEFAUT",
            },
        })
    response.raise_for_status()
    results = response.json().get("results", [])

    found = []
    for item in results:
        item_nature = item.get("nature", "")
        # Defensive client-side check — never trust an unverified server-side
        # filter alone, especially given we're deliberately excluding
        # décrets/arrêtés/ordonnances, which would number in the hundreds.
        if item_nature and item_nature != nature:
            continue
        titles = item.get("titles", [])
        first_title = titles[0] if titles else {}
        date_pub = item.get("datePublication", "") or ""

        found.append({
            "id": first_title.get("cid", ""),
            "title": first_title.get("title", ""),
            "date": date_pub[:10] if date_pub else "",
            "nature": item_nature,
        })

    return found


def search_jorf_texts(
    start_date: str,
    end_date: str,
    nature: str = "DECRET",
    page_size: int = 100,
    max_pages: int = 30,
) -> list[dict]:
    """
    Enumerate every JORF text of a given nature in a date window, paginating.

    Differs from search_jorf_by_date_range in two ways: it pages through the
    whole result set rather than taking the first page, and it defaults to
    DECRET. It exists for décret d'application tracking (decree_tracker.py),
    where the point is precisely to sweep the document types the discovery
    poller deliberately skips.

    Why enumerate rather than query by law number: the Légifrance /search
    full-text field (typeChamp "TEXTE") does not work — verified 2026-09-03
    against production, where searching "titres-restaurant" returned horse
    racing results from 2008 and "aide à mourir" returned 2022 décrets on
    officiers ministériels. The text criterion is silently ignored and
    arbitrary rows come back. Only the NATURE and DATE_PUBLICATION *facet
    filters* are honoured, so date+nature enumeration plus local matching is
    the only reliable path. Don't "optimise" this back into a text query
    without re-verifying that behaviour.

    Args:
        start_date / end_date: ISO date strings.
        nature: "DECRET", "ARRETE", "ORDONNANCE", ...
        page_size: server caps this at 100.
        max_pages: safety bound. At ~100-250 décrets/month a multi-year
            sweep will hit this — raise it deliberately rather than by
            accident, since each page is an API call (and, until
            _get_token() is cached, two).

    Returns:
        List of {id, title, date, nature}, oldest first. Stops early on the
        first short page. Never raises on an empty result set.
    """
    collected: list[dict] = []
    seen_ids: set[str] = set()

    for page in range(1, max_pages + 1):
        response = _post("/search", {
                "fond": "JORF",
                "recherche": {
                    "champs": [],
                    "filtres": [
                        {"facette": "NATURE", "valeurs": [nature]},
                        {"facette": "DATE_PUBLICATION", "dates": {"start": start_date, "end": end_date}},
                    ],
                    "pageNumber": page,
                    "pageSize": page_size,
                    "operateur": "ET",
                    "sort": "DATE_ASC",
                    "typePagination": "DEFAUT",
                },
            })
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            break

        for item in results:
            item_nature = item.get("nature", "")
            if item_nature and item_nature != nature:
                continue
            titles = item.get("titles", [])
            first_title = titles[0] if titles else {}
            text_id = first_title.get("cid", "")
            if not text_id or text_id in seen_ids:
                continue
            seen_ids.add(text_id)
            date_pub = item.get("datePublication", "") or ""
            collected.append({
                "id": text_id,
                "title": first_title.get("title", ""),
                "date": date_pub[:10] if date_pub else "",
                "nature": item_nature,
            })

        if len(results) < page_size:
            break

    return collected
