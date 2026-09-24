#!/bin/bash
# Stellifi Fund I dashboard + warehouse sync — deploy.
#   ./deploy.sh            # publish index.html to GCS (the dashboard)
#   ./deploy.sh --job      # also build + deploy the Cloud Run job + daily scheduler
#   ./deploy.sh --views    # (re)create the oth_gold views
#   ./deploy.sh --sync     # run one sync now (local ADC)
set -euo pipefail
cd "$(dirname "$0")"
PROJECT=oth-data-warehouse; REGION=us-central1; JOB=stellifi-fund-sync
gsutil -h "Cache-Control:private, max-age=300" cp index.html gs://oth-dashboard/stellifi/index.html
echo "✅ dashboard → https://storage.cloud.google.com/oth-dashboard/stellifi/index.html"
for a in "$@"; do case "$a" in
  --job)
    gcloud run jobs deploy "$JOB" --source=. --project="$PROJECT" --region="$REGION" --tasks=1 --max-retries=1 --task-timeout=300 --memory=512Mi
    PROJ_NUM=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
    SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"
    gcloud scheduler jobs describe "${JOB}-daily" --location="$REGION" --project="$PROJECT" >/dev/null 2>&1 \
      && gcloud scheduler jobs update http "${JOB}-daily" --location="$REGION" --project="$PROJECT" --schedule="15 6 * * *" --time-zone="America/Chicago" \
           --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" --http-method=POST --oauth-service-account-email="$SA" \
      || gcloud scheduler jobs create http "${JOB}-daily" --location="$REGION" --project="$PROJECT" --schedule="15 6 * * *" --time-zone="America/Chicago" \
           --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB}:run" --http-method=POST --oauth-service-account-email="$SA"
    echo "✅ job $JOB + scheduler ${JOB}-daily (6:15am CT)";;
  --views) bq query --project_id="$PROJECT" --use_legacy_sql=false < gold_views.sql && echo "✅ gold views";;
  --sync)  python3 sync_to_bq.py;;
esac; done
