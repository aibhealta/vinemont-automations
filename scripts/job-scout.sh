#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${RD_OPTION_CONFIG_PATH:-/opt/automations/current/config/job-scout.json}"
STATE_PATH="${RD_OPTION_STATE_PATH:-/opt/automations/job-scout/state/postings.json}"
RESULT_PATH="${RD_OPTION_RESULT_PATH:-/opt/automations/job-scout/results/latest.json}"
EXECUTION_ID="${RD_OPTION_EXECUTION_ID:-${RD_JOB_EXECID:-${RD_JOB_EXECUTIONID:-manual}}}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --execution-id)
      EXECUTION_ID="${2:?missing value for --execution-id}"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

exec python3 /opt/automations/current/scripts/job-scout.py \
  --config "$CONFIG_PATH" \
  --state "$STATE_PATH" \
  --result "$RESULT_PATH" \
  --execution-id "$EXECUTION_ID"
