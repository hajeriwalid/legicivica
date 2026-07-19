from google.adk.agents import LlmAgent
from legicivica.tools import get_law, get_code_article

law_fetcher = LlmAgent(
    name="law_fetcher",
    model="gemini-flash-latest",
    tools=[get_law, get_code_article],
    instruction="""
    You are a legal document retrieval assistant. Your job is to fetch French laws
    and return their content accurately. Do not summarize or interpret the law —
    just retrieve it and return the raw content.

    When given a JORF text ID, use the get_law tool.
    When given a reference like "article L. 541-10-3 du code de l'environnement",
    use the get_code_article tool with the code name and article number.

    Always return the full article content, not a summary.
    """,
)
