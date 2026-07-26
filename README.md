# LegiCivica

**An open-source experiment in making French law legible to citizens, the day it's published.**

New French laws are rarely self-contained — they're edits to an existing body of
legislation ("*le premier alinéa de l'article L. 541-10-3 est ainsi modifié...*"),
often deferring the substance to ministerial decrees, spanning multiple codes,
and transposing EU directives without summarizing them. LegiCivica is an
AI-agent pipeline, built on [Google ADK](https://google.github.io/adk-docs/) and
the official [Légifrance API](https://piste.gouv.fr/), that tries to close that
gap: fetch a new law, resolve everything it references, explain what actually
changed in plain language, and classify who's affected — so a citizen, an NGO,
or a journalist can understand a law's real impact without a law degree. It could also provide
a score to the new law according to defined criteria such as transparency, rule of law, etc.

This is a public, in-progress build. The code and its failures are documented
together, as a [blog series](#following-along), on purpose.

## Status

| Stage | What it does | Status |
|---|---|---|
| **Fetch** | Retrieve a law from the Journal Officiel, or any article from any French code | ✅ Built |
| **Resolve** | Recursively follow every article a law references, deduplicated and depth/budget-bounded | ✅ Built |
| **Explain** | Turn the resolved law + references into a plain-language explanation of what changed | 🧭 Designed, not yet built |
| **Classify** | Determine who's affected, compare to the equivalent EU directive, score transparency | 🧭 Designed, not yet built |
| **Discover & deploy** | Poll for newly published laws, run the pipeline unattended, notify subscribers | 🧭 Designed, not yet built |

## Getting started

### Prerequisites

- Python 3.13+
- A [PISTE](https://piste.gouv.fr/) account with an application subscribed to
  the **Légifrance** API (sandbox access is free — see the
  [PISTE help center](https://piste.gouv.fr/help-center/guide) if the
  subscription checkbox is greyed out, you likely need to accept the CGU first)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/)

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

# Run the ADK agent end to end
python main.py
```

## Project structure

```
legicivica/
├── legicivica/
│   ├── agents/
│   │   └── pipeline.py         # ADK agent definitions (models, tools, instructions)
│   └── tools/
│       ├── legifrance.py       # Légifrance API client — no ADK dependency, testable alone
│       ├── reference_parser.py # Regex-based extractor for French code-article references
│       ├── resolver.py         # Recursive, breadth-first reference resolver
│       └── __init__.py         # Agent-facing tool wrappers (the docstrings the model reads)
├── main.py                     # Entry point — wires the agent + runner together
├── test_client.py              # Smoke test for the Légifrance API client
├── test_resolver.py            # Smoke test for the reference resolver
└── requirements.txt
```

`legifrance.py` → `reference_parser.py` / `resolver.py` → `tools/__init__.py` →
`agents/pipeline.py` → `main.py`: each layer only depends on the one below it,
so any piece can be tested in isolation before it's wired into an agent.

## How the reference resolver works, briefly

A new law amends article A; article A itself references article B; B
references C, and so on. The resolver fetches a law, uses a regex-based parser
to find every code article it references, and walks outward breadth-first —
everything one hop away is resolved before anything two hops away — up to a
configurable `max_depth` and `max_articles`. Every article is fetched at most
once no matter how many other articles point to it, and anything discovered but
not followed (depth or budget exceeded) is reported, not silently dropped.

```python
from legicivica.tools.resolver import resolve_law_references

result = resolve_law_references("JORFTEXT000054399113", max_depth=2, max_articles=25)
# result["root"], result["resolved"], result["errors"],
# result["skipped_max_depth"], result["skipped_max_articles"], result["corrections"]
```

## Following along

This project is being built and written about at the same time — the blog
series documents the design decisions and the real bugs found while testing
against live Légifrance data:

1. [Democratizing the Law with AI Agents](https://www.linkedin.com/pulse/new-laws-dont-make-sense-own-ai-agents-could-fix-walid-hajeri-60bue/) — why this project exists
2. [LegiCivica - scaffolding a french law AI Agent](https://www.linkedin.com/pulse/legicivica-scaffolding-french-law-ai-agent-walid-hajeri-xycre/) — the v0 agent and project structure

## Data & license

Légifrance data is sourced from [DILA](https://www.dila.premier-ministre.gouv.fr/)
via the official [PISTE API](https://piste.gouv.fr/), under the
[Etalab Open License 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).

This project's code is licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/legalcode) — see
[LICENSE](LICENSE). Share it, adapt it, credit it; not for commercial use.

---

*Disclaimer: this is a personal, experimental project. Views expressed in the
accompanying blog series are my own and are not shared, supported, or endorsed
by my current employer.*
