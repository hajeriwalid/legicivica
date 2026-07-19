import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.pipeline import law_fetcher

APP_NAME = "legicivica"
USER_ID = "user"


async def main():
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)

    runner = Runner(
        agent=law_fetcher,
        app_name=APP_NAME,
        session_service=session_service,
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=(
            "Fetch the law JORFTEXT000054399113 and tell me how many articles it has "
            "and what the first article says."
        ))],
    )

    for event in runner.run(user_id=USER_ID, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    print(part.text)


asyncio.run(main())
