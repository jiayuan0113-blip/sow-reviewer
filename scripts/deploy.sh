#!/usr/bin/env bash
# Deploys sow_reviewer to Agent Engine (Agent Runtime). Costs money.
#
#   GOOGLE_CLOUD_PROJECT=my-project GOOGLE_CLOUD_LOCATION=us-central1 scripts/deploy.sh
#
# Optional: SOW_OTEL_TO_CLOUD=0 to skip Cloud Trace; AGENT_ENGINE_ID=123 to update in place.
# data/ and tools/ are staged by sow_reviewer/.agent_engine_config.json, because
# `adk deploy` otherwise copies only the agent folder.
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
: "${GOOGLE_CLOUD_LOCATION:?set GOOGLE_CLOUD_LOCATION}"

cd "$(dirname "$0")/.."

# adk deploy copies sow_reviewer/.env into the runtime environment.
if [ -f sow_reviewer/.env ] && grep -Eq '^GOOGLE_GENAI_USE_ENTERPRISE=(0|false|FALSE)' sow_reviewer/.env; then
  echo "sow_reviewer/.env selects AI Studio. Set GOOGLE_GENAI_USE_ENTERPRISE=1 and remove GOOGLE_API_KEY first." >&2
  exit 1
fi

args=(
  --project "$GOOGLE_CLOUD_PROJECT"
  --region "$GOOGLE_CLOUD_LOCATION"
  --display_name sow-reviewer
  --description "Source-of-wealth document review (synthetic data only)"
)
if [ "${SOW_OTEL_TO_CLOUD:-1}" = "1" ]; then
  args+=(--otel_to_cloud)
fi
if [ -n "${AGENT_ENGINE_ID:-}" ]; then
  args+=(--agent_engine_id "$AGENT_ENGINE_ID")
fi

uv run --extra gcp adk deploy agent_engine "${args[@]}" sow_reviewer
