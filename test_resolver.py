from legicivica.tools.resolver import resolve_law_references

# Resolve the ultra-fast fashion law and everything it references
result = resolve_law_references("JORFTEXT000054399113", max_depth=2, max_articles=250)

root = result["root"]
print(f"Root law: {root['title']}")
print(f"Published: {root['date']}")
print(f"Own articles: {len(root['articles'])}")
print()

print(f"Resolved {len(result['resolved'])} referenced articles (max_depth={result['params']['max_depth']}, max_articles={result['params']['max_articles']}):")
for art in result["resolved"]:
    print(f"  [depth {art['depth']}] {art['code']} — art. {art['num']}  (via {', '.join(art['referenced_by'])})")
    print(f"      {art['content'][:120]}...")

if result["errors"]:
    print()
    print(f"Errors ({len(result['errors'])}):")
    for err in result["errors"]:
        print(f"  [depth {err['depth']}] {err['code']} — art. {err['num']}: {err['error']}")

if result["skipped_max_depth"]:
    print()
    print(f"Skipped — beyond max_depth ({len(result['skipped_max_depth'])}):")
    for s in result["skipped_max_depth"]:
        print(f"  {s['code']} — art. {s['num']} (via {', '.join(s['referenced_by'])})")

if result["skipped_max_articles"]:
    print()
    print(f"Skipped — max_articles budget spent ({len(result['skipped_max_articles'])}):")
    for s in result["skipped_max_articles"]:
        print(f"  {s['code']} — art. {s['num']} (via {', '.join(s['referenced_by'])})")

if result["corrections"]:
    print()
    print(f"Corrections — cross-article code override ({len(result['corrections'])}):")
    for c in result["corrections"]:
        print(f"  art. {c['num']}: resolved as {c['resolved_as']!r}, corrected to {c['corrected_to']!r} (depth {c['depth']})")
