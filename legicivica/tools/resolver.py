from legicivica.tools.legifrance import fetch_law_text, search_code_article
from legicivica.tools.reference_parser import extract_references


def resolve_law_references(text_id: str, max_depth: int = 2, max_articles: int = 25) -> dict:
    """
    Fetch a law and recursively resolve every code article it references,
    following references-within-references up to `max_depth` hops away
    from the law, and never fetching the same article twice.

    This is the recursive-fetch problem described in the article: a new
    law amends article A, article A itself references article B, and so
    on. Left alone, that chain can be arbitrarily deep and wide, so both
    dimensions are bounded explicitly rather than left to run until the
    API rate-limits us.

    Traversal is breadth-first: all references found directly in the
    law's own articles are depth 1; references found inside those
    articles are depth 2; etc. BFS (rather than recursing depth-first)
    means that when the same article is reachable via two different
    paths, it is always resolved at the shallowest depth it actually
    appears at, and is never fetched more than once regardless of how
    many other articles reference it.

    Before any fetching happens, the law's own articles are also scanned
    once to build a registry of every article number that was given an
    *explicit* code name somewhere in the text (e.g. "l'article
    L. 541-9-1 du code de l'environnement"). Any reference resolved
    without an explicit code — a bare number carried forward, or a "du
    même code" that landed on the wrong code because it sits inside a
    quoted, inserted provision — is then checked against that registry
    and corrected if a different, explicit code is on record for the
    same number elsewhere in the law. This is what fixes the case
    described in the article: "du même code" inside an amendment to the
    code de la consommation can misattribute an article that every other
    mention in the same law explicitly ties to the code de
    l'environnement. Every correction is recorded, not applied silently.

    Args:
        text_id: The JORF text identifier of the root law, e.g.
            "JORFTEXT000054399113".
        max_depth: How many hops of references to follow beyond the
            law's own articles. 0 disables resolution (root law only,
            references listed but not fetched). Each additional level
            multiplies the number of API calls, so keep this small.
        max_articles: Hard cap on the number of *referenced* articles
            fetched (the root law's own articles don't count against
            this). Once reached, remaining discovered references are
            recorded as skipped rather than fetched.

    Returns:
        A dict with:
          - root: the law, as returned by fetch_law_text()
          - resolved: list of successfully fetched referenced articles,
            each with {code, num, depth, referenced_by, ...article data}
          - errors: references that were looked up but failed (article
            not found, wrong code, API error), with {code, num, depth, error}
          - skipped_max_depth: references discovered beyond max_depth,
            never fetched, with {code, num, depth, referenced_by}
          - skipped_max_articles: references that would have been
            fetched next but the max_articles budget was already spent
          - corrections: non-explicit references whose code was
            overridden because a different, explicit code was found for
            the same article number elsewhere in the law, with
            {num, resolved_as, corrected_to, depth}
          - params: the max_depth / max_articles used for this run
    """
    root = fetch_law_text(text_id)

    resolved = []
    errors = []
    skipped_max_depth = []
    skipped_max_articles = []
    corrections = []
    visited = set()
    budget_exhausted = False

    # Every article number that was given an explicit code name somewhere
    # in the law's own text, first occurrence wins. Cheap extra pass over
    # text already in memory — no extra API calls.
    explicit_code_by_num = {}
    for article in root["articles"]:
        for ref in extract_references(article["content"]):
            if ref["explicit"]:
                explicit_code_by_num.setdefault(ref["num"], ref["code"])

    def resolve_code(ref: dict, depth: int) -> str:
        code = ref["code"]
        if not ref["explicit"]:
            known = explicit_code_by_num.get(ref["num"])
            if known and known != code:
                corrections.append({
                    "num": ref["num"],
                    "resolved_as": code,
                    "corrected_to": known,
                    "depth": depth,
                })
                code = known
        return code

    # Seed the frontier (depth 1) from the root law's own articles.
    frontier = {}
    for article in root["articles"]:
        for ref in extract_references(article["content"]):
            code = resolve_code(ref, depth=1)
            key = (code, ref["num"])
            frontier.setdefault(key, {"code": code, "num": ref["num"], "referenced_by": set()})
            frontier[key]["referenced_by"].add(f"{root['id']} art. {article['num']}")

    depth = 1
    while frontier and depth <= max_depth:
        next_frontier = {}

        for key, ref in frontier.items():
            if key in visited:
                continue
            visited.add(key)

            if len(resolved) >= max_articles:
                budget_exhausted = True
                skipped_max_articles.append({
                    "code": ref["code"],
                    "num": ref["num"],
                    "depth": depth,
                    "referenced_by": sorted(ref["referenced_by"]),
                })
                continue

            try:
                article = search_code_article(ref["code"], ref["num"])
            except Exception as exc:
                errors.append({
                    "code": ref["code"],
                    "num": ref["num"],
                    "depth": depth,
                    "error": str(exc),
                })
                continue

            if "error" in article:
                errors.append({
                    "code": ref["code"],
                    "num": ref["num"],
                    "depth": depth,
                    "error": article["error"],
                })
                continue

            resolved.append({
                **article,
                # search_code_article's own "code" field comes from API
                # response metadata and can be blank; the code we searched
                # with is always authoritative for what this article is.
                "code": ref["code"],
                "depth": depth,
                "referenced_by": sorted(ref["referenced_by"]),
            })

            if depth < max_depth:
                for sub_ref in extract_references(article["content"], default_code=ref["code"]):
                    sub_code = resolve_code(sub_ref, depth=depth + 1)
                    sub_key = (sub_code, sub_ref["num"])
                    if sub_key in visited:
                        continue
                    next_frontier.setdefault(
                        sub_key,
                        {"code": sub_code, "num": sub_ref["num"], "referenced_by": set()},
                    )
                    next_frontier[sub_key]["referenced_by"].add(f"{ref['code']} art. {ref['num']}")

        frontier = next_frontier
        depth += 1

    # Anything still queued once we've run out of depth or budget is a
    # reference we know about but deliberately did not follow.
    if frontier:
        for ref in frontier.values():
            bucket = skipped_max_articles if budget_exhausted else skipped_max_depth
            bucket.append({
                "code": ref["code"],
                "num": ref["num"],
                "depth": depth,
                "referenced_by": sorted(ref["referenced_by"]),
            })

    return {
        "root": root,
        "resolved": resolved,
        "errors": errors,
        "skipped_max_depth": skipped_max_depth,
        "skipped_max_articles": skipped_max_articles,
        "corrections": corrections,
        "params": {"max_depth": max_depth, "max_articles": max_articles},
    }
