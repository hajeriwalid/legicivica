"""
Alternative to test_explainer.py: grounds the explainer via session state
and a {resolved_law} instruction placeholder instead of build_explainer_prompt.
Does not touch pipeline.py / build_explainer_prompt / test_explainer.py.
"""

import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.schemas import LawExplanation
from legicivica.tools.resolver import resolve_law_references

APP_NAME = "legicivica"
USER_ID = "user"

# Same brief as explainer_agent in pipeline.py, but grounded through a
# {resolved_law} state placeholder instead of a hand-built prompt string.
explainer_agent_state = LlmAgent(
    name="explainer_state_variant",
    model="gemini-pro-latest",
    output_schema=LawExplanation,
    instruction="""
    You are a legal explainer for French law. The full resolved law data —
    the law's own amendment instructions plus the current text of every
    reference that was resolved — is given below as a Python dict:

    {resolved_law}

    Ground every explanation strictly in that data. Never rely on your own
    prior knowledge of French law.

    For each resolved reference that the law meaningfully changes:
      - Quote or closely paraphrase the article's relevant passage in
        in_its_own_words.
      - Explain what the amendment does to it in what_changes, as a
        before/after where the text allows it.
      - Give one concrete example of the change in effect in example.
      - Set status to "self_executing" or "awaiting_decree", with
        awaiting_detail spelling out exactly what's undefined if the latter.

    Omit references that are purely procedural pointers with no real
    amendment of their own. Write summary and every field in plain language.
    """,
)


async def main():
    resolver_result = resolve_law_references("JORFTEXT000054399113", max_depth=1, max_articles=15)

    session_service = InMemorySessionService()

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        state={"resolved_law": resolver_result},
    )

    print("=" * 88)
    print("WHAT {resolved_law} ACTUALLY EXPANDS TO — str(value), per ADK's own")
    print("inject_session_state code (instructions_utils.py: `return str(value)`)")
    print("=" * 88)
    print(str(resolver_result))
    print()

    runner = Runner(agent=explainer_agent_state, app_name=APP_NAME, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text="Explain this law.")])

    raw_output = None
    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    raw_output = part.text

    if raw_output is None:
        print("No response from the explainer agent.")
        return

    explanation = LawExplanation.model_validate_json(raw_output)

    print(f"Law: {explanation.law_title}")
    print(f"Summary: {explanation.summary}")
    print()

    print(f"Explained references ({len(explanation.references)}):")
    for ref in explanation.references:
        print(f"  --- {ref.code} — art. {ref.num} [{ref.status}] ---")
        print(f"  In its own words: {ref.in_its_own_words[:150]}...")
        print(f"  What changes: {ref.what_changes}")
        print(f"  Example: {ref.example}")
        if ref.awaiting_detail:
            print(f"  Awaiting decree: {ref.awaiting_detail}")
        print()


asyncio.run(main())
