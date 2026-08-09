import os

from google.cloud import firestore

_db = None


def get_db() -> firestore.Client:
    """
    Return a cached Firestore client, created on first use.

    Reads the target project from GCP_PROJECT_ID — set explicitly rather
    than relying on ambient gcloud config, so local runs and the deployed
    Cloud Run containers behave identically regardless of what's active in
    a developer's gcloud CLI. Locally, this expects
    `gcloud auth application-default login` to have been run once (or a
    downloaded service-account key referenced via
    GOOGLE_APPLICATION_CREDENTIALS) — Firestore has no concept of a
    sandbox/production split the way PISTE does, so there is only ever one
    real database to point at.
    """
    global _db
    if _db is None:
        project_id = os.getenv("GCP_PROJECT_ID")
        if not project_id:
            raise RuntimeError(
                "GCP_PROJECT_ID is not set. Firestore has no default project to "
                "fall back to outside of an already-configured gcloud environment."
            )
        _db = firestore.Client(project=project_id)
    return _db


def law_exists(jorf_id: str) -> bool:
    """
    Check whether a law has already been processed.

    This is the resolver's dedup mechanism for the poller — checked before
    ever invoking the (costly, multi-LLM-call) pipeline, using the JORF id
    as the Firestore document ID rather than a separate index.
    """
    return get_db().collection("laws").document(jorf_id).get().exists


def save_law(law_record: dict) -> None:
    """
    Persist one processed law's full record — transparency score, civic
    index, affected parties, summary — under laws/{jorf_id}.

    law_record must contain a "jorf_id" key; that value becomes the
    document ID. Adds a server-side "processed_at" timestamp automatically
    — the caller doesn't need to supply one.
    """
    jorf_id = law_record["jorf_id"]
    payload = {**law_record, "processed_at": firestore.SERVER_TIMESTAMP}
    get_db().collection("laws").document(jorf_id).set(payload)


def list_laws() -> list[dict]:
    """
    Return every processed law, ordered by publication date ascending —
    the order the UI's chart needs to plot score evolution over time.
    """
    docs = get_db().collection("laws").order_by("publication_date").stream()
    return [doc.to_dict() for doc in docs]


def list_laws_missing_translation() -> list[dict]:
    """
    Return every processed law that has no "fr" field yet — the backfill
    script's worklist. A plain Python filter over list_laws() rather than a
    Firestore query, since "field does not exist" isn't something Firestore's
    query API can express directly and this collection is small.
    """
    return [law for law in list_laws() if "fr" not in law]


def save_translation(jorf_id: str, fr_data: dict) -> None:
    """
    Attach (or replace) a law's French translation sub-document without
    touching any other field — unlike save_law(), which overwrites the whole
    document, this is a merge so the backfill script can't accidentally drop
    the English content it's translating from.
    """
    get_db().collection("laws").document(jorf_id).set({"fr": fr_data}, merge=True)


def get_poll_state() -> str | None:
    """
    Return the daily poller's watermark (last_checked_date, an ISO date
    string), or None if the poller has never run (first-ever run / a fresh
    Firestore database).
    """
    doc = get_db().collection("meta").document("poll_state").get()
    if not doc.exists:
        return None
    return doc.to_dict().get("last_checked_date")


def set_poll_state(last_checked_date: str) -> None:
    """Update the daily poller's watermark after a run completes."""
    get_db().collection("meta").document("poll_state").set(
        {"last_checked_date": last_checked_date}
    )
