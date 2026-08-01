import asyncio
import json

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.pipeline import explainer_agent, build_explainer_prompt, list_unresolved_references
from legicivica.agents.schemas import LawExplanation
from legicivica.tools.resolver import resolve_law_references

APP_NAME = "legicivica"
USER_ID = "user"


async def main():
    resolver_result = resolve_law_references("JORFTEXT000054399113", max_depth=1, max_articles=15)
    prompt = build_explainer_prompt(resolver_result)
    unresolved = list_unresolved_references(resolver_result)

    print("=" * 88)
    print("BUILD_EXPLAINER_PROMPT — INPUT (raw resolver_result dict)")
    print("=" * 88)
    print(json.dumps(resolver_result, indent=2, ensure_ascii=False))
    print()

    print("=" * 88)
    print("BUILD_EXPLAINER_PROMPT — OUTPUT (the actual prompt sent to the explainer agent)")
    print("=" * 88)
    print(prompt)
    print()

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=explainer_agent, app_name=APP_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    # True non-blocking async generator — see main.py for why runner.run()
    # (sync) would be a problem the moment this isn't a standalone script.
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

    if unresolved:
        print(f"Unresolved references ({len(unresolved)}) — not explained, listed as-is:")
        for u in unresolved:
            print(f"  {u['code']} — art. {u['num']} ({u['reason']})")


asyncio.run(main())
