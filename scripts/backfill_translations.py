"""
One-time (or re-runnable) backfill: translate every already-processed law
that doesn't have a French version yet.

    python scripts/backfill_translations.py

Cheap relative to the original pipeline run — this only translates
already-generated text (one gemini-flash-latest call per law), it doesn't
re-run resolve/explain/classify/civic. Safe to re-run: list_laws_missing_translation()
only returns laws that still lack an "fr" field, so already-translated laws
are skipped.
"""

import asyncio
import logging
import sys

from legicivica.agents.translator import translate_law_to_french
from legicivica.storage.firestore_store import list_laws_missing_translation, save_translation

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_translations")


async def main() -> int:
    laws = list_laws_missing_translation()
    logger.info("%d law(s) missing a French translation", len(laws))

    translated, failed = 0, 0
    for law in laws:
        jorf_id = law.get("jorf_id", "")
        try:
            fr_data = await translate_law_to_french(law)
            save_translation(jorf_id, fr_data)
            translated += 1
            logger.info("translated %s — %s", jorf_id, law.get("title", "")[:80])
        except Exception:
            failed += 1
            logger.exception("failed to translate %s — will retry next run", jorf_id)

    logger.info("done: %d translated, %d failed", translated, failed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
