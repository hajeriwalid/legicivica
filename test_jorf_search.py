from datetime import date, timedelta

from legicivica.tools.legifrance import search_jorf_by_date_range

# Search the last 8 weeks of JORF for actual laws (not décrets/arrêtés).
# Against sandbox, expect fixture-quality data at best — this just confirms
# the request shape is accepted (no 400) and the response parses cleanly.
# Against production, eyeball the titles/dates against legifrance.gouv.fr/jorf
# before trusting this for a real backfill.
end_date = date.today().isoformat()
start_date = (date.today() - timedelta(weeks=8)).isoformat()

results = search_jorf_by_date_range(start_date, end_date, nature="LOI")

print(f"Window: {start_date} .. {end_date}")
print(f"Found {len(results)} LOI text(s):")
for r in results:
    print(f"  {r['id']} — {r['date']} — {r['title'][:100]}")
