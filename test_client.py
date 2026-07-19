from legicivica.tools.legifrance import fetch_law_text, search_code_article

# Fetch the ultra-fast fashion law
law = fetch_law_text("JORFTEXT000054399113")
print(f"Title: {law['title']}")
print(f"Date: {law['date']}")
print(f"Number of articles: {len(law['articles'])}")
print()

# Fetch a referenced article
article = search_code_article("code de l'environnement", "L. 541-10-3")
print(f"Article {article['num']} from {article['code']}:")
print(article['content'][:500])
