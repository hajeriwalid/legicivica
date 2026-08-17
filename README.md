# LegiCivica

**An open-source experiment in making French law legible to citizens, the day it's published.**

New French laws are rarely self-contained, they're edits to an existing body of
legislation ("*le premier alinéa de l'article L. 541-10-3 est ainsi modifié...*"),
often deferring the substance to ministerial decrees, spanning multiple codes,
and transposing EU directives without summarizing them. LegiCivica is an
AI-agent pipeline, built on [Google ADK](https://google.github.io/adk-docs/) and
the official [Légifrance API](https://piste.gouv.fr/), that tries to close that
gap: fetch a new law, resolve everything it references, generate automated text summaries and map textual edits — assisting citizens, NGOs, and journalists in navigating legislative changes through an open academic lens. It could also evaluate alignment against normative political science criteria (e.g., rule of law benchmarks, structural transparency metrics).

If you need to understand how it was built, check [blog series](#following-along), on purpose.

## Status

| Stage | What it does | Status |
|---|---|---|
| **Fetch** | Retrieve a law from the Journal Officiel, or any article from any French code | ✅ Built |
| **Resolve** | Recursively follow every article a law references, deduplicated and depth/budget-bounded | ✅ Built |
| **Explain** | Turn the resolved law + references into a plain-language, grounded explanation of what changed | ✅ Built |
| **Classify & score** | Determine who's affected, score transparency, and assess against 5 scoped civic/rule-of-law criteria | ✅ Built |
| **Orchestrate** | Wire fetch → resolve → explain → classify/civic (parallel) → assemble into one ADK `Workflow` graph | ✅ Built |
| **Discover** | Search JORF by date range + document nature, dedupe against already-processed laws, run the pipeline unattended | ✅ Built |
| **Dashboard** | Public read-only UI — table + chart of transparency/civic scores over time | ✅ Built |
| **Deploy** | Containerize + run on GCP (Cloud Run, Cloud Scheduler, Firestore) | Running on GCP |

## Getting started

### Prerequisites

- Python 3.13+
- A [PISTE](https://piste.gouv.fr/) account with an application subscribed to
  the **Légifrance** API (sandbox access is free — see the
  [PISTE help center](https://piste.gouv.fr/help-center/guide) if the
  subscription checkbox is greyed out, you likely need to accept the CGU first)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/)
- Optional, only for the discovery poller / dashboard: a GCP project with
  Firestore enabled, and `gcloud auth application-default login` run once
  locally. The core agent pipeline (`main.py`, every `test_*.py`) needs none
  of this.

### Setup

```bash
git clone https://github.com/hajeriwalid/legicivica.git
cd legicivica

python3.13 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# then fill in PISTE_CLIENT_ID, PISTE_CLIENT_SECRET, and GOOGLE_API_KEY
```

`PISTE_SANDBOX=true` (the default) points at Légifrance's sandbox environment.
Sandbox data is useful for verifying the plumbing but can serve fixture content
loosely related to what you actually asked for — don't trust it for real
answers. Flip it to `false` with production credentials once you have them.

### Running it

```bash
# Sanity-check the Légifrance API client directly
python test_client.py

# Run the reference resolver: fetch a law and recursively resolve
# everything it references, deduplicated and depth/budget-bounded
python test_resolver.py

# Run the explainer agent: resolve a law, then have Gemini turn it into a
# grounded, plain-language, structured explanation of what changed
python test_explainer.py

# Run the classifier + civic-health agents: who's affected, transparency
# score, and a scoped civic/rule-of-law index — chained by hand
python test_classifier.py

# Run the same thing as an actual ADK Workflow graph — explainer, then
# classifier and civic-health in parallel, joined, then assembled
python test_workflow.py

# Run the conversational agent end to end (fetch a law by natural-language request)
python main.py

# Search JORF for actual laws (not décrets/arrêtés) published in a date window
python test_jorf_search.py

# The discovery poller — requires GCP_PROJECT_ID and Firestore access.
# --mode backfill seeds historical data; --mode daily is what runs on a
# schedule once deployed. Both need production PISTE credentials
# (PISTE_SANDBOX=false) to find real, current laws.
python scripts/run_pipeline.py --mode backfill --weeks 8 --cap 20

# The dashboard — reads whatever run_pipeline.py has written to Firestore
streamlit run app/streamlit_app.py
```

## Project structure

```
legicivica/
├── legicivica/
│   ├── agents/
│   │   ├── pipeline.py         # ADK agent definitions (law_fetcher, explainer, classifier, civic) + prompt building
│   │   ├── schemas.py          # Pydantic output schemas each agent is constrained to
│   │   └── orchestrator.py     # The real ADK Workflow graph — nodes, edges, JoinNode fan-in
│   ├── tools/
│   │   ├── legifrance.py       # Légifrance API client — no ADK dependency, testable alone
│   │   ├── reference_parser.py # Regex-based extractor for French code-article references
│   │   ├── resolver.py         # Recursive, breadth-first reference resolver
│   │   ├── scoring.py          # Deterministic transparency score + civic index assembly
│   │   └── __init__.py         # Agent-facing tool wrappers (the docstrings the model reads)
│   └── storage/
│       └── firestore_store.py  # Firestore data access — law dedup/save/list, poller watermark
├── scripts/
│   └── run_pipeline.py         # Discovery poller / historical backfill entrypoint
├── app/
│   └── streamlit_app.py        # Public read-only dashboard — table + score-evolution chart
├── main.py                     # Entry point — conversational law_fetcher demo
├── test_client.py              # Smoke test for the Légifrance API client
├── test_resolver.py            # Smoke test for the reference resolver
├── test_explainer.py           # Smoke test for the explainer agent
├── test_classifier.py          # Smoke test for classifier + civic-health agents, hand-chained
├── test_workflow.py            # Runs the same chain as an actual Workflow graph
├── test_jorf_search.py         # Smoke test for JORF date-range/nature search
├── Dockerfile.poller            # Container for scripts/run_pipeline.py (Cloud Run Jobs)
├── Dockerfile.ui                 # Container for app/streamlit_app.py (Cloud Run Service)
├── DEPLOY.md                    # GCP deployment runbook (Cloud Shell, step by step)
└── requirements.txt
```

`legifrance.py` → `reference_parser.py` / `resolver.py` → `tools/__init__.py` →
`agents/pipeline.py` → `agents/orchestrator.py` → `main.py` / `test_*.py`: each
layer only depends on the one below it, so any piece can be tested in
isolation before it's wired into an agent or a graph. `orchestrator.py` wires
`explainer_agent` → (`classifier_agent`, `civic_agent` in parallel) →
`assemble_reports` into one `Workflow` object — replacing the hand-chained
`async def main()` style still used by `test_classifier.py` for comparison.
`main.py`'s `law_fetcher` runs standalone, outside the workflow, for
natural-language requests.

## How the pieces fit into a workflow

`explainer_agent`, `classifier_agent`, and `civic_agent` are wired into one
ADK `Workflow` graph rather than called by hand in sequence. Nodes can be
plain functions or `LlmAgent`s; a plain tuple in the chain is a sequential
step, a nested tuple is a parallel fan-out, and a `JoinNode` is required
wherever a later node needs to wait for every branch of a fan-out to finish
— a plain node otherwise fires as soon as the first branch completes:

```python
impact_pipeline = Workflow(
    name="impact_pipeline",
    edges=[
        (
            "START",
            resolve_and_build_prompt,
            explainer_agent,
            build_classifier_prompt_node,
            (classifier_agent, civic_agent),   # parallel — neither depends on the other
            reports_ready,                     # JoinNode — waits for both branches
            assemble_reports,
        )
    ],
)
```

Data crosses more than one hop via `ctx.state` / `output_key`, not just the
previous node's return value — see `orchestrator.py` for the full graph and
`test_workflow.py` to run it.

## Discovery, storage, and the dashboard

`scripts/run_pipeline.py` runs `impact_pipeline` unattended, in two modes:

```bash
python scripts/run_pipeline.py --mode backfill --weeks 8 --cap 20   # historical seed data
python scripts/run_pipeline.py --mode daily                          # what a scheduler runs
```

Both modes call `search_jorf_by_date_range()` (`legifrance.py`) — restricted
to `nature="LOI"` by default, since every other JORF document type
(décrets, arrêtés, ordonnances) would number in the hundreds over the same
window. Discovered ids are deduplicated against Firestore
(`legicivica/storage/firestore_store.py`) using the JORF id itself as the
document ID — a law is "already processed" iff `laws/{jorf_id}` exists — so
daily runs can safely re-scan a lookback buffer without reprocessing
anything. Each new law is run through `impact_pipeline` exactly like
`test_workflow.py`, then saved as one Firestore document.

`app/streamlit_app.py` is a read-only dashboard over the same `laws`
collection: a line chart of both scores over time, a table, and a per-law
detail view (summary, every score component's reasoning, affected
parties). See [`DEPLOY.md`](DEPLOY.md) for running this on GCP — Cloud Run
Jobs for the poller, Cloud Scheduler to trigger the daily run, Cloud Run
Service for the dashboard, all free-tier-eligible.

## Following along

This project is being built and written about at the same time — the blog
series documents the design decisions and the real bugs found while testing
against live Légifrance data:

1. [Democratizing the Law with AI Agents](https://www.linkedin.com/pulse/new-laws-dont-make-sense-own-ai-agents-could-fix-walid-hajeri-60bue/) — why this project exists
2. [From Text to a Graph: How LegiCivica Resolves Nested Legal References](https://www.linkedin.com/pulse/from-text-graph-how-legicivica-resolves-nested-legal-walid-hajeri-xyc6e/) — the reference parser and recursive resolver
3. [LegiCivica - scaffolding a french law AI Agent](https://www.linkedin.com/pulse/legicivica-scaffolding-french-law-ai-agent-walid-hajeri-xycre/) — the v0 agent and project structure
4. [Beyond Data Retrieval: Teaching an Agent to Reason About New Laws](https://www.linkedin.com/pulse/beyond-data-retrieval-teaching-agent-reason-new-laws-walid-hajeri-mvvse/) — the explainer agent
5. [Scoring the Rule of Law - Wiring Multiple AI Agents Into a Workflow](https://www.linkedin.com/pulse/scoring-rule-law-wiring-multiple-ai-agents-workflow-walid-hajeri-ovpzf/) — classifier, civic-health agent, and the ADK Workflow orchestrator

## Data & license

Légifrance data is sourced from [DILA](https://www.dila.premier-ministre.gouv.fr/)
via the official [PISTE API](https://piste.gouv.fr/), under the
[Etalab Open License 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).

This project's code is licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/legalcode) — see
[LICENSE](LICENSE). Share it, adapt it, credit it; not for commercial use.


## ⚠️ Disclaimer & Legal Notice / Avertissement Légal

### English
**This repository is an open-source research experiment in civic tech**
* **Not Legal Advice:** LegiCivica generates automated, AI-assisted text summaries and normative policy scores. It does **not** provide legal advice, legal consultations, or legal characterization under any domestic jurisdiction. 
* **No Judicial Weight:** Output scores measure alignment with open international civic frameworks; they do not assess formal legal validity, enforceability, or constitutionality.
* **AI & API Limitations:** Summaries and reference mappings are generated by Large Language Models and automated API parsing; they may contain errors or hallucinated content. Users must independently verify all information against official publications in the *Journal Officiel*.
* **EU AI Act Notice (Regulation EU 2024/1689):** This software is an automated processing system designed for educational and civic research purposes only.

### Français
**Ce dépôt est une expérimentation open-source de recherche en civic tech**
* **Absence de conseil juridique :** LegiCivica produit des synthèses textuelles automatisées et des évaluations normatives. Il ne fournit **aucun conseil juridique** ni aucune consultation legale. 
* **Absence de valeur juridique :** Les données et scores produits ne jugent ni de la constitutionnalité, ni de la validité juridique, ni de l'opposabilité des textes (compétence exclusive des juridictions.
* **Limites de l'IA :** Les résumés et résolutions de références sont issus de traitements automatisés et de modèles d'IA. Ils peuvent comporter des inexactitudes et erreur. Seuls les textes publiés au *Journal Officiel* font foi.

---

*Disclaimer: this is a personal, experimental project. Views expressed in the
accompanying blog series are my own and are not shared, supported, or endorsed
by my current employer.*
