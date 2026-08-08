import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from legicivica.agents.pipeline import (
    build_classifier_prompt,
    build_explainer_prompt,
    civic_agent,
    classifier_agent,
    explainer_agent,
)
from legicivica.agents.schemas import CivicHealthAssessment, ImpactClassification, LawExplanation
from legicivica.tools.resolver import resolve_law_references
from legicivica.tools.scoring import build_civic_report, build_transparency_report

APP_NAME = "legicivica"
USER_ID = "user"


async def run_agent(agent, prompt: str, schema):
    """Run one ADK agent to completion and parse its structured output."""
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name=APP_NAME, user_id=USER_ID)
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    raw_output = None
    async for event in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=message):
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    raw_output = part.text

    if raw_output is None:
        raise RuntimeError(f"No response from agent '{agent.name}'.")
    return schema.model_validate_json(raw_output)


async def main():
    resolver_result = resolve_law_references("JORFTEXT000054399113", max_depth=1, max_articles=15)

    explainer_prompt = build_explainer_prompt(resolver_result)
    explanation = await run_agent(explainer_agent, explainer_prompt, LawExplanation)

    # Same input shape for both agents — build_classifier_prompt is reused
    # as-is, no separate prompt builder needed for the civic assessment.
    classifier_prompt = build_classifier_prompt(explanation)
    classification = await run_agent(classifier_agent, classifier_prompt, ImpactClassification)
    civic_assessment = await run_agent(civic_agent, classifier_prompt, CivicHealthAssessment)

    report = build_transparency_report(explanation, resolver_result, classification)
    civic_report = build_civic_report(civic_assessment)

    print(f"Law: {report['law_title']}")
    print()

    print(f"Who's affected ({len(report['affected_parties'])}):")
    for party in report["affected_parties"]:
        date = f", effective {party.effective_date}" if party.effective_date else ""
        print(f"  [{party.category}] {party.obligation}{date}")
    print()

    if report["eu_directives_referenced"]:
        print(f"EU directives referenced (detected, not compared) ({len(report['eu_directives_referenced'])}):")
        for d in report["eu_directives_referenced"]:
            print(f"  {d}")
        print()

    print(f"Transparency score: {report['overall_score']}/{report['overall_max']}")
    for c in report["components"]:
        print(f"  [{c['score']}/5] {c['label']} — {c['reason']}")
    print()

    print(f"Civic index: {civic_report['civic_index']:+d} (range {civic_report['civic_index_range']})")
    for c in civic_report["criteria"]:
        print(f"  [{c['score']:+d}] {c['label']} — {c['reason']}")
    print()
    print(civic_report["notice"])


asyncio.run(main())
