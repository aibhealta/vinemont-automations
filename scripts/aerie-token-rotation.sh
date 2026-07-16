#!/usr/bin/env bash
# aerie-token-rotation.sh
#
# Rotates Aerie's scoped Rundeck API token (user `aerie`, role `aerie`).
#
# Must run inside the Rundeck container where:
#   /opt/dashboard-secrets/dashboard-compose.env — the only writable secret
#   /opt/automations/     — mounted rw; result artifacts land here
#
# Required env:
#   RD_OPTION_MANAGER_TOKEN  — API token for the aerie-token-manager account.
#                              Loaded from the dedicated secret directory; the
#                              Rundeck secure option may override it.
#
# Optional env overrides:
#   RUNDECK_URL          — default: http://localhost:4440
#   DASHBOARD_STACK_ID   — Dockhand stack ID for the dashboard (default: 54)
#
# Usage: aerie-token-rotation.sh [--dry-run]
#   --dry-run  Validate inputs and show intended actions without minting or writing.

set -euo pipefail

# ── constants ──────────────────────────────────────────────────────────────────

RUNDECK_URL="${RUNDECK_URL:-http://localhost:4440}"
DASHBOARD_STACK_ID="${DASHBOARD_STACK_ID:-54}"  # also set via RD_OPTION_DASHBOARD_STACK_ID
SECRETS_FILE="/opt/dashboard-secrets/dashboard-compose.env"
RESULTS_DIR="/opt/automations/aerie-token-rotation/results"
RESULTS_FILE="$RESULTS_DIR/latest.json"
EXECUTION_ID="${RD_JOB_EXECID:-unknown}"
RESULT_WRITTEN=0
FAILURE_DETAIL="automation exited before completing"
TMPFILE=""
DOCKHAND_COOKIE=""

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1
[[ "${RD_OPTION_DRY_RUN:-}" == "true" ]] && DRY_RUN=1

# ── helpers ───────────────────────────────────────────────────────────────────

log()  { echo "[$(date -u +%H:%M:%SZ)] $*"; }
die()  { FAILURE_DETAIL="$*"; log "ERROR: $*"; exit 1; }

write_result() {
  local status="$1" expiry_date="$2" detail="${3:-}"
  mkdir -p "$RESULTS_DIR"
  local completed_at
  completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local tmp_file
  tmp_file=$(mktemp "${RESULTS_FILE}.tmp.XXXXXX")
  local execution_json status_json completed_json expiry_json blocks_json detail_json
  execution_json=$(json_string "$EXECUTION_ID")
  status_json=$(json_string "$status")
  completed_json=$(json_string "$completed_at")
  expiry_json=$(json_string "$expiry_date")
  blocks_json="[]"
  if [[ -n "$detail" ]]; then
    detail_json=$(json_string "$detail")
    blocks_json="[{\"type\":\"message\",\"text\":$detail_json}]"
  fi
  printf '{\n' > "$tmp_file"
  printf '  "schema": "aerie.automation.result.v1",\n' >> "$tmp_file"
  printf '  "automation_id": "aerie-token-rotation",\n' >> "$tmp_file"
  printf '  "execution_id": %s,\n' "$execution_json" >> "$tmp_file"
  printf '  "status": %s,\n' "$status_json" >> "$tmp_file"
  printf '  "completed_at": %s,\n' "$completed_json" >> "$tmp_file"
  printf '  "summary": {"label": "Next expiry", "value": %s},\n' "$expiry_json" >> "$tmp_file"
  printf '  "blocks": %s\n' "$blocks_json" >> "$tmp_file"
  printf '}\n' >> "$tmp_file"
  mv "$tmp_file" "$RESULTS_FILE"
  RESULT_WRITTEN=1
  log "Result artifact written: status=$status expiry=$expiry_date"
}

json_string() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

json_first_array_id() {
  perl -0ne 'if (/"id"\s*:\s*"([^"]*)"/) { print $1 }' <<< "$1"
}

json_object_field() {
  local field="$1" input="$2"
  FIELD="$field" perl -0ne '
    my $field = quotemeta($ENV{"FIELD"});
    if (/"$field"\s*:\s*"((?:\\.|[^"])*)"/) {
      my $value = $1;
      $value =~ s/\\"/"/g;
      $value =~ s/\\\\/\\/g;
      print $value;
    }
  ' <<< "$input"
}

write_failure_artifact_on_exit() {
  local code=$?
  [[ -n "${TMPFILE:-}" ]] && rm -f "$TMPFILE"
  [[ -n "${DOCKHAND_COOKIE:-}" ]] && rm -f "$DOCKHAND_COOKIE"
  if [[ "$code" -ne 0 && "$RESULT_WRITTEN" -eq 0 ]]; then
    set +e
    write_result failure "failed" "$FAILURE_DETAIL"
  fi
  exit "$code"
}

trap write_failure_artifact_on_exit EXIT

# ── prerequisite checks ───────────────────────────────────────────────────────

if [[ -z "${RD_OPTION_MANAGER_TOKEN:-}" && -f /opt/dashboard-secrets/aerie-token-manager.env ]]; then
  # shellcheck disable=SC1091
  source /opt/dashboard-secrets/aerie-token-manager.env
fi
[[ -n "${RD_OPTION_MANAGER_TOKEN:-}" ]] || die "RD_OPTION_MANAGER_TOKEN must be set"
[[ -f "$SECRETS_FILE" ]] || die "secrets file not found: $SECRETS_FILE"

# Override stack ID from job option if provided
[[ -n "${RD_OPTION_DASHBOARD_STACK_ID:-}" ]] && DASHBOARD_STACK_ID="$RD_OPTION_DASHBOARD_STACK_ID"

# Read Dockhand credentials from the secrets file (they live alongside RUNDECK_TOKEN)
_extract() { grep -m1 "^${1}=" "$SECRETS_FILE" | cut -d= -f2- | tr -d '"' || true; }
dockhand_url=$(_extract  DOCKHAND_URL)
dockhand_user=$(_extract DOCKHAND_USERNAME)
dockhand_pass=$(_extract DOCKHAND_PASSWORD)

[[ -n "$dockhand_url" ]]  || die "DOCKHAND_URL not found in $SECRETS_FILE"
[[ -n "$dockhand_user" ]] || die "DOCKHAND_USERNAME not found in $SECRETS_FILE"
[[ -n "$dockhand_pass" ]] || die "DOCKHAND_PASSWORD not found in $SECRETS_FILE"

MGR_AUTH="X-Rundeck-Auth-Token: ${RD_OPTION_MANAGER_TOKEN}"

log "aerie-token-rotation starting (dry_run=$DRY_RUN)"
log "Rundeck: $RUNDECK_URL | Dockhand: $dockhand_url | Stack ID: $DASHBOARD_STACK_ID"

# ── 1. list existing aerie tokens ─────────────────────────────────────────────

log "Listing tokens for user 'aerie'..."
tokens_json=$(curl -fsSL \
  -H "$MGR_AUTH" -H "Accept: application/json" \
  "$RUNDECK_URL/api/49/tokens/aerie") || die "failed to list tokens for user 'aerie'"

old_token_id=$(json_first_array_id "$tokens_json") || die "failed to parse token list"

log "Existing token id: ${old_token_id:-none found}"

# ── dry-run exit ──────────────────────────────────────────────────────────────

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "DRY RUN: would mint new 30d token for user 'aerie' with role 'aerie'"
  log "DRY RUN: would update RUNDECK_TOKEN in $SECRETS_FILE"
  log "DRY RUN: would trigger Dockhand redeploy of stack $DASHBOARD_STACK_ID via $dockhand_url"
  log "DRY RUN: would verify new token (list jobs 200, create project 403, read ACLs 403)"
  log "DRY RUN: would leave the old token to expire naturally after the overlap window"
  write_result success "dry-run"
  exit 0
fi

# ── 2. mint new token ─────────────────────────────────────────────────────────

log "Minting new 30d token for user 'aerie'..."
mint_response=$(curl -fsSL -X POST \
  -H "$MGR_AUTH" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"user":"aerie","roles":["aerie"],"duration":"30d"}' \
  "$RUNDECK_URL/api/49/tokens") || die "failed to mint new token"

new_token=$(json_object_field token "$mint_response") || die "failed to parse new token value"
[[ -n "$new_token" ]] || die "new token response did not include token value"

new_token_id=$(json_object_field id "$mint_response") || die "failed to parse new token id"
[[ -n "$new_token_id" ]] || die "new token response did not include token id"

expiry_raw=$(json_object_field expiration "$mint_response")

# Extract date portion (YYYY-MM-DD) for the result summary
expiry_date="${expiry_raw:0:10}"

log "Minted new token id=$new_token_id expiry=$expiry_raw"

# ── 3. write new token to secrets file ───────────────────────────────────────

log "Updating RUNDECK_TOKEN in $SECRETS_FILE..."
BACKUP="${SECRETS_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp "$SECRETS_FILE" "$BACKUP"

# Atomic replace: write to a temp file on the same fs, then rename
TMPFILE=$(mktemp "${SECRETS_FILE}.tmp.XXXXXX")
cleanup_tmp() { rm -f "$TMPFILE"; }

grep -v "^RUNDECK_TOKEN=" "$SECRETS_FILE" > "$TMPFILE"
printf "RUNDECK_TOKEN=%s\n" "$new_token" >> "$TMPFILE"
mv "$TMPFILE" "$SECRETS_FILE"

token_line_count=$(grep -c "^RUNDECK_TOKEN=" "$SECRETS_FILE" || true)
if [[ "$token_line_count" -ne 1 ]]; then
  log "ERROR: expected 1 RUNDECK_TOKEN line, got $token_line_count — restoring backup"
  cp "$BACKUP" "$SECRETS_FILE"
  # Revoke the orphaned new token since the file was restored
  curl -fsSL -X DELETE -H "$MGR_AUTH" "$RUNDECK_URL/api/49/token/$new_token_id" || true
  die "secrets file write failed — new token revoked"
fi
log "Secrets file updated (backup: $BACKUP)"

# ── 4. trigger dashboard redeploy ────────────────────────────────────────────

log "Triggering Dockhand redeploy of dashboard stack $DASHBOARD_STACK_ID..."
DOCKHAND_COOKIE=$(mktemp /tmp/aerie-dockhand-cookie.XXXXXX)

dockhand_login_payload=$(printf '{"username":%s,"password":%s}' "$(json_string "$dockhand_user")" "$(json_string "$dockhand_pass")")
login_http=$(curl -fsSL -o /dev/null -w "%{http_code}" \
  -c "$DOCKHAND_COOKIE" \
  -H "Content-Type: application/json" \
  -d "$dockhand_login_payload" \
  "${dockhand_url}/api/auth/login") || {
  log "ERROR: Dockhand login failed — restoring backup, revoking new token"
  cp "$BACKUP" "$SECRETS_FILE"
  curl -fsSL -X DELETE -H "$MGR_AUTH" "$RUNDECK_URL/api/49/token/$new_token_id" || true
  die "Dockhand login failed — old token remains active"
}

if [[ "$login_http" != "200" ]]; then
  log "ERROR: Dockhand login returned HTTP $login_http — restoring backup, revoking new token"
  cp "$BACKUP" "$SECRETS_FILE"
  curl -fsSL -X DELETE -H "$MGR_AUTH" "$RUNDECK_URL/api/49/token/$new_token_id" || true
  die "Dockhand login returned $login_http — old token remains active"
fi

deploy_http=$(curl -fsSL -o /dev/null -w "%{http_code}" -X POST \
  -b "$DOCKHAND_COOKIE" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  "${dockhand_url}/api/git/stacks/${DASHBOARD_STACK_ID}/deploy") || {
  log "ERROR: Dockhand redeploy request failed — restoring backup, revoking new token"
  cp "$BACKUP" "$SECRETS_FILE"
  curl -fsSL -X DELETE -H "$MGR_AUTH" "$RUNDECK_URL/api/49/token/$new_token_id" || true
  die "Dockhand redeploy failed — old token remains active"
}

if [[ "$deploy_http" != "200" && "$deploy_http" != "202" ]]; then
  log "ERROR: Dockhand returned HTTP $deploy_http — restoring backup, revoking new token"
  cp "$BACKUP" "$SECRETS_FILE"
  curl -fsSL -X DELETE -H "$MGR_AUTH" "$RUNDECK_URL/api/49/token/$new_token_id" || true
  die "Dockhand redeploy returned $deploy_http — old token remains active"
fi
log "Redeploy triggered (HTTP $deploy_http). Waiting 30s for dashboard to restart..."
sleep 30

# ── 5. verify new token ───────────────────────────────────────────────────────

log "Verifying new token scope..."
VERIFY_FAILED=0

_check() {
  local label="$1" expected="$2" url="$3"
  shift 3
  local code
  code=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "X-Rundeck-Auth-Token: ${new_token}" \
    -H "Accept: application/json" \
    "$@" "$url")
  if [[ "$code" == "$expected" ]]; then
    log "  ✓ $label: HTTP $code"
  else
    log "  ✗ $label: HTTP $code (expected $expected)"
    VERIFY_FAILED=1
  fi
}

_check "list jobs"      200 "$RUNDECK_URL/api/49/project/homelab/jobs"
_check "create project" 403 "$RUNDECK_URL/api/49/projects" \
  -X POST -d '{"name":"verify-probe"}' -H "Content-Type: application/json"
_check "read ACLs"      403 "$RUNDECK_URL/api/49/system/acl/"

if [[ "$VERIFY_FAILED" -eq 1 ]]; then
  log "Verification failed — operator must intervene manually."
  log "  Old token id:           ${old_token_id:-none}"
  log "  New (active) token id:  $new_token_id"
  log "  The secrets file and dashboard already use the new token."
  log "  If the new token is mis-scoped, mint a corrected one and rerun manually."
  write_result failure "$expiry_date"
  exit 1
fi

log "Token verification passed."

# The dashboard has no unauthenticated endpoint that proves which Rundeck token
# its running process loaded. Keep the old 30-day token alive for the remaining
# overlap window instead of risking an outage after an ineffective redeploy.
log "Old token ${old_token_id:-none} remains valid until its natural expiry."

# ── 6. write result artifact ──────────────────────────────────────────────────

write_result success "$expiry_date"
log "aerie-token-rotation complete."
