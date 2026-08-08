from google.adk.agents import Context
from google.adk.workflow import JoinNode, Workflow

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


def resolve_and_build_prompt(ctx: Context, node_input: str) -> str:
    """
    First node in the graph. node_input is the JORF text id the workflow was
    started with (the Runner's initial message, treated as plain text).

    Writes resolver_result into workflow state under that name so any later
    node can bind it by parameter name — not just the one immediately after
    this one in the chain. Returns the built prompt string, which becomes
    the next node's node_input (explainer_agent's message).
    """
    resolver_result = resolve_law_references(node_input, max_depth=1, max_articles=15)
    ctx.state["resolver_result"] = resolver_result
    return build_explainer_prompt(resolver_result)


def build_classifier_prompt_node(explanation: LawExplanation) -> str:
    """
    explanation is bound from workflow state — explainer_agent's output_key
    put it there. Returns the classifier prompt, which becomes node_input
    for both classifier_agent and civic_agent (the fan-out that follows this
    node in the graph) — one prompt, two independent readings of it, since
    neither agent depends on the other's output.
    """
    return build_classifier_prompt(explanation)


def assemble_reports(
    resolver_result: dict,
    explanation: LawExplanation,
    classification: ImpactClassification,
    civic_assessment: CivicHealthAssessment,
) -> dict:
    """
    Fan-in node — runs once classifier_agent AND civic_agent have both
    completed. None of its parameters come from node_input (ambiguous with
    two incoming branches); all four are bound from workflow state, written
    by earlier nodes regardless of how many hops back they ran.
    """
    return {
        "transparency": build_transparency_report(explanation, resolver_result, classification),
        "civic": build_civic_report(civic_assessment),
    }


# A nested tuple only declares the fan-out edges (build_classifier_prompt_node
# -> classifier_agent, -> civic_agent). It does NOT make the next node in the
# chain wait for both branches — a plain function node triggers on the FIRST
# predecessor to finish, not all of them. Confirmed by running this without
# the join below: assemble_reports fired the moment classifier_agent
# finished, with civic_agent still mid-flight, and crashed on a missing
# civic_assessment. reports_ready is a JoinNode — the construct that
# actually waits for every incoming edge before letting the graph continue.
reports_ready = JoinNode(name="reports_ready")

# The graph: fetch + resolve -> explain -> classify and assess civic health
# in parallel (fan-out, since neither depends on the other) -> wait for both
# -> assemble both reports. Sequential and parallel aren't separate agent
# classes here — a tuple in the chain is a sequential step, a nested tuple
# is a fan-out, inferred purely from position.
impact_pipeline = Workflow(
    name="impact_pipeline",
    edges=[
        (
            "START",
            resolve_and_build_prompt,
            explainer_agent,
            build_classifier_prompt_node,
            (classifier_agent, civic_agent),
            reports_ready,
            assemble_reports,
        )
    ],
)
