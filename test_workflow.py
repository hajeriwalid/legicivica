import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.orchestrator import impact_pipeline

APP_NAME = "legicivica"
USER_ID = "user"


async def main():
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=impact_pipeline, app_name=APP_NAME, session_service=session_service)

    message = types.Content(role="user", parts=[types.Part(text="JORFTEXT000054399113")])

    final_output = None
    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        print(f"[event] author={event.author!r} output={'yes' if event.output is not None else 'no'}")
        if event.output is not None:
            final_output = event.output

    if final_output is None:
        print("No final output from the workflow.")
        return

    transparency = final_output["transparency"]
    civic = final_output["civic"]

    print()
    print(f"Law: {transparency['law_title']}")
    print()

    print(f"Transparency score: {transparency['overall_score']}/{transparency['overall_max']}")
    for c in transparency["components"]:
        print(f"  [{c['score']}/5] {c['label']} — {c['reason']}")
    print()

    print(f"Civic index: {civic['civic_index']:+d} (range {civic['civic_index_range']})")
    for c in civic["criteria"]:
        print(f"  [{c['score']:+d}] {c['label']} — {c['reason']}")


asyncio.run(main())
