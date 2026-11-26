#!/usr/bin/env bash
# Deploy ReadMind to Google Cloud Run (builds the Dockerfile with Cloud Build).
#
# Prerequisites (once):
#   gcloud auth login && gcloud config set project <PROJECT_ID>
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
#   printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
#   (grant the Cloud Run runtime service account roles/secretmanager.secretAccessor on that secret)
#
# Usage:  bash deploy/cloud_run.sh [region] [service-name]
set -euo pipefail
REGION="${1:-asia-south1}"
SERVICE="${2:-readmind}"
cd "$(dirname "$0")/.."

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 --timeout 900 --concurrency 1 --max-instances 2 \
  --set-env-vars "READMIND_LLM=gemini,READMIND_MODEL=${READMIND_MODEL:-gemini-3.5-flash},READMIND_RPM=${READMIND_RPM:-10}" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
echo "Deployed: $URL"
curl -s "$URL/api/health" && echo
