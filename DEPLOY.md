# Deploying LegiCivica

This covers two things:
1. **What any deployment needs, regardless of platform** — the actual
   architecture is deliberately simple and not tied to one vendor.
2. **A concrete, tested walkthrough on Google Cloud** — because that's
   what this project was built and verified against, not because the code
   requires it.

If you're deploying somewhere else, read the "What you need" section, then
substitute your platform's equivalents for the GCP-specific steps.

## What you need (platform-agnostic)

The system is three pieces:

| Piece | What it is | Any equivalent works |
|---|---|---|
| **A scheduled batch job** | Runs `scripts/run_pipeline.py --mode daily` once a day. Finds newly published laws, runs them through the agent pipeline, writes results. | Any cron-triggered container: a scheduled task on your platform of choice, a GitHub Actions cron workflow, a VPS crontab calling `docker run`, etc. |
| **An always-on web service** | Serves `app/streamlit_app.py` — the public read-only dashboard. Needs to stay reachable over HTTP; scale-to-zero is a nice-to-have, not a requirement. | Any container-hosting service, a PaaS with a Dockerfile deploy path, or a plain VM running the container. |
| **A document store** | Holds processed laws (`legicivica/storage/firestore_store.py`), keyed by JORF id for dedup, plus one small "poll watermark" document. | Any document/NoSQL database reachable from both the job and the service. Swapping this out means rewriting `firestore_store.py`'s ~5 functions against a different client — nothing else in the codebase depends on Firestore specifically. |

Plus, everywhere: **a secrets store** for `PISTE_CLIENT_ID`,
`PISTE_CLIENT_SECRET`, and `GOOGLE_API_KEY` (never bake these into an
image or commit them), and **a container registry** to push
`Dockerfile.ui` and `Dockerfile.poller` to.

Both Dockerfiles already build clean, portable images — `docker build -f
Dockerfile.ui .` / `docker build -f Dockerfile.poller .` work anywhere
Docker runs. The only thing genuinely specific to the walkthrough below is
which managed services host those two images, the schedule, the database,
and the secrets.

## The Google Cloud walkthrough

This is the path actually used to build and run this project: **Cloud
Run** (a Service for the dashboard, a Job for the poller), **Firestore**
for storage, **Cloud Scheduler** for the daily trigger, and **Secret
Manager** for credentials. All of it fits comfortably inside GCP's Always
Free tier at this project's scale — see the "Cost monitoring" note at the
end.

Every command below uses `PROJECT_ID` and `REGION` as placeholders —
substitute your own GCP project ID and a region (Firestore's location
choice is **permanent**, so check available locations before committing to
one — see step 2).

### Prerequisites

1. **A GCP project with billing enabled** (required even to stay within
   the Always Free tier).
2. **PISTE credentials** from [piste.gouv.fr](https://piste.gouv.fr) — this
   walkthrough runs with `PISTE_SANDBOX=true` deliberately. Sandbox search
   returns plausible, correctly-shaped data, which is enough to prove the
   pipeline out; it hasn't been independently verified against the
   official JORF the way production access would guarantee. Request
   production PISTE access separately if you need verified-current data,
   then flip `PISTE_SANDBOX=false` in both Cloud Run Jobs (step 7).
3. **A Gemini API key** (`GOOGLE_API_KEY`) — already required for the core
   pipeline, from [Google AI Studio](https://aistudio.google.com/).

### Cost and timing, so `--task-timeout` isn't a guess

One real, timed run of `impact_pipeline` against a single law (sandbox
PISTE + real Gemini, resolver depth 1 / budget 15) took **~2.5–3 minutes**,
almost entirely two Gemini Pro calls (explainer, then classifier + civic
in parallel). `scripts/run_pipeline.py` processes laws **sequentially** —
at that rate, a 20-law backfill is roughly 50–60 minutes. The job timeouts
below reflect this measurement, not a round-number guess.

### Steps

```bash
gcloud config set project PROJECT_ID

# 1. Enable APIs
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  firestore.googleapis.com secretmanager.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com

# 2. Firestore — location choice is PERMANENT, check first
gcloud firestore locations list
gcloud firestore databases create --location=REGION --type=firestore-native

# 3. Artifact Registry
gcloud artifacts repositories create legicivica --repository-format=docker --location=REGION

# 4. Secrets (paste real values interactively — never hardcode in scripts)
printf '%s' "$PISTE_CLIENT_ID"     | gcloud secrets create PISTE_CLIENT_ID     --data-file=-
printf '%s' "$PISTE_CLIENT_SECRET" | gcloud secrets create PISTE_CLIENT_SECRET --data-file=-
printf '%s' "$GOOGLE_API_KEY"      | gcloud secrets create GOOGLE_API_KEY      --data-file=-

# 5. Dedicated, least-privilege runtime service account
gcloud iam service-accounts create legicivica-runner --display-name="LegiCivica Cloud Run runtime"
for S in PISTE_CLIENT_ID PISTE_CLIENT_SECRET GOOGLE_API_KEY; do
  gcloud secrets add-iam-policy-binding $S \
    --member="serviceAccount:legicivica-runner@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:legicivica-runner@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# 6. Build images (Cloud Build — no local Docker needed).
#    `gcloud builds submit --tag ... -f Dockerfile.X` does NOT work —
#    --tag mode only supports a file literally named `Dockerfile`, no way
#    to point it at a custom name. Use the small cloudbuild.*.yaml configs
#    in this repo instead, which invoke `docker build -f <name>` as an
#    explicit build step.
gcloud builds submit --config=cloudbuild.poller.yaml \
  --substitutions=_IMAGE=REGION-docker.pkg.dev/PROJECT_ID/legicivica/poller .
gcloud builds submit --config=cloudbuild.ui.yaml \
  --substitutions=_IMAGE=REGION-docker.pkg.dev/PROJECT_ID/legicivica/ui .

# 7. Cloud Run Jobs — daily + backfill, same image, different --args.
#    --task-timeout values reflect the ~2.5-3 min/law measurement above,
#    not a round-number guess.
gcloud run jobs create legicivica-poller-daily \
  --image=REGION-docker.pkg.dev/PROJECT_ID/legicivica/poller --region=REGION \
  --service-account=legicivica-runner@PROJECT_ID.iam.gserviceaccount.com \
  --set-secrets=PISTE_CLIENT_ID=PISTE_CLIENT_ID:latest,PISTE_CLIENT_SECRET=PISTE_CLIENT_SECRET:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest \
  --set-env-vars=PISTE_SANDBOX=true \
  --args="--mode=daily" --max-retries=1 --task-timeout=20m

gcloud run jobs create legicivica-poller-backfill \
  --image=REGION-docker.pkg.dev/PROJECT_ID/legicivica/poller --region=REGION \
  --service-account=legicivica-runner@PROJECT_ID.iam.gserviceaccount.com \
  --set-secrets=PISTE_CLIENT_ID=PISTE_CLIENT_ID:latest,PISTE_CLIENT_SECRET=PISTE_CLIENT_SECRET:latest,GOOGLE_API_KEY=GOOGLE_API_KEY:latest \
  --set-env-vars=PISTE_SANDBOX=true \
  --args="^,^--mode=backfill,--weeks=8,--cap=20" --max-retries=0 --task-timeout=75m

# 8. Run backfill once, manually, watch logs
gcloud run jobs execute legicivica-poller-backfill --region=REGION --wait
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=legicivica-poller-backfill" --limit=100

# 9. Cloud Scheduler → triggers the daily job
gcloud scheduler jobs create http legicivica-daily-poll \
  --location=REGION --schedule="17 6 * * *" \
  --uri="https://REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT_ID/jobs/legicivica-poller-daily:run" \
  --http-method=POST \
  --oauth-service-account-email=legicivica-runner@PROJECT_ID.iam.gserviceaccount.com

# 10. UI as a Cloud Run Service (public, scale-to-zero)
gcloud run deploy legicivica-ui \
  --image=REGION-docker.pkg.dev/PROJECT_ID/legicivica/ui --region=REGION \
  --service-account=legicivica-runner@PROJECT_ID.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --max-instances=2 --port=8080

gcloud run services describe legicivica-ui --region=REGION --format='value(status.url)'
```

**Notes from actually running this:**
- **`--args` needs the `^,^` custom delimiter prefix**
  (`--args="^,^--mode=backfill,--weeks=8,--cap=20"`) — a plain comma-joined
  string without it doesn't parse into separate argv entries. Confirmed
  via `gcloud run jobs describe`.
- **Step 9's Scheduler → Cloud Run Jobs trigger** (the exact REST path,
  API version, and required IAM role) has shifted across `gcloud`/API
  versions in the past. The command above is the form confirmed working.
  Some older GCP projects require `gcloud app create --region=...` before
  Scheduler accepts its first job (a legacy App Engine location quirk) —
  if step 9 fails on a location error, this is the likely cause.

## Redeploying after a code change

No rebuild-from-scratch needed. From the repo root:
```bash
gcloud builds submit --config=cloudbuild.ui.yaml \
  --substitutions=_IMAGE=REGION-docker.pkg.dev/PROJECT_ID/legicivica/ui .
gcloud run deploy legicivica-ui \
  --image=REGION-docker.pkg.dev/PROJECT_ID/legicivica/ui --region=REGION \
  --service-account=legicivica-runner@PROJECT_ID.iam.gserviceaccount.com \
  --allow-unauthenticated --min-instances=0 --max-instances=2 --port=8080
```
Same pattern with `cloudbuild.poller.yaml` for the poller image — the two
existing Jobs pick up a new `:latest` push automatically on their next
run, no redeploy needed unless you're also changing `--args`, timeouts, or
env vars.

## Verification

**Local, before touching any cloud infra:**
1. `python test_jorf_search.py` against sandbox first (confirms the
   request shape is accepted), then once with production credentials to
   manually verify real recent LOI titles/dates.
2. Authenticate locally against your document store (for Firestore:
   `gcloud auth application-default login`), then
   `python scripts/run_pipeline.py --mode backfill --weeks 1 --cap 2`
   end to end — verify the 2 resulting documents have the full expected
   shape.
3. `streamlit run app/streamlit_app.py` locally against the same store —
   confirm the table and chart render.

**Post-deploy:**
1. Run the backfill job for real; tail its logs.
2. Spot-check 2-3 saved law documents directly in your document store.
3. **Open the live dashboard URL in a real browser, not just `curl`.** A
   bare HTTP GET only fetches Streamlit's static HTML/JS shell — it never
   opens a session, so the app script (including its imports) never
   actually runs. A missing-`PYTHONPATH` bug reached production this way
   once: `curl` returned HTTP 200 and the platform's logs stayed clean
   both before and after a real user hit a `ModuleNotFoundError`, rendered
   as an in-app traceback in their browser that never surfaced in the
   request logs. Confirm the table and chart actually render, not just
   that the server process started.
4. Run the daily job once manually — zero-new-laws is an expected pass,
   it just confirms the daily code path runs clean in the deployed
   container.
5. Let the scheduler fire unattended at least once — check its next
   run/last-run status the following day without manual intervention.
   This is the actual proof the autonomy requirement is met.

## Known, deliberately unfixed risks

- `legifrance.py`'s `_get_token()` fetches a fresh OAuth token on *every*
  API call, uncached. At 20 sequential backfill laws (each triggering
  several resolver calls), this could hit PISTE token-endpoint rate
  limits — watch for it during a large backfill and revisit if it causes
  failures. Not fixed here; it would touch existing, already-tested code
  outside this phase's scope.
- Occasionally the same law appears under two different JORFTEXT ids in
  JORF search results (a legitimate correction/*rectificatif* republishing
  pattern) — observed directly during development. Dedup is by JORFTEXT
  id, so both would be processed and stored as separate entries. Not
  worth engineering around for this phase.

## Cost monitoring

Two separate cost surfaces to watch, regardless of platform:

- **LLM usage (Gemini)** is the only genuinely uncapped, usage-based cost
  in this system — every law processed makes several Gemini Pro calls.
  Everything else below scales with a generous free tier at this
  project's traffic (a handful of new laws a day at most). Check usage at
  [aistudio.google.com](https://aistudio.google.com/), not just your
  general cloud billing report — API-key usage can be tracked separately
  from project-level billing depending on how the key was provisioned.
- **Container registry storage** creeps up slowly as you push new image
  versions over time; prune old, unreferenced image digests periodically.

If you're on GCP specifically: set a budget alert (Billing → Budgets &
alerts) at a low threshold (e.g. $5-10) — Cloud Run, Cloud Run Jobs,
Firestore, Cloud Scheduler, and Secret Manager all comfortably fit inside
the Always Free tier at this project's scale, so an alert firing almost
certainly means the Gemini usage line, not infra.
