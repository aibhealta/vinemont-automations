# Token Rotation — aerie-token-rotation

The `aerie-token-rotation` automation (group `aerie/ops`, job name `aerie-token-rotation`)
renews Aerie's scoped Rundeck API token before the 30-day expiry window.

## What the automation does

1. Authenticates as `aerie-token-manager` using the dedicated secret directory or the
   secure Rundeck job option.
2. Captures the current token id for audit logging.
3. Mints a new 30-day token for user `aerie`, role `aerie`.
4. Atomically replaces `RUNDECK_TOKEN=` in `/opt/dashboard-secrets/dashboard-compose.env`
   (backup: `dashboard-compose.env.bak.YYYYMMDDHHMMSS`).
5. Triggers a Dockhand redeploy of the dashboard stack (stack ID 54 by default) so the container
   picks up the new token value at startup.
6. Waits 30 seconds, then verifies the new token:
   - `GET /api/49/project/homelab/jobs` → 200 (must be allowed)
   - `POST /api/49/projects` → 403 (must be denied)
   - `GET /api/49/system/acl/` → 403 (must be denied)
7. Leaves the old token to expire naturally, preserving a safe overlap window.
8. Writes `/opt/automations/aerie-token-rotation/results/latest.json` with the result schema.

## Schedule

Runs at 03:00 UTC on the 1st and 22nd of each month (every ~21 days). The token is
valid for 30 days; this schedule provides 8+ days of buffer before expiry.

## Failure modes and recovery

| Failure point | Effect | Recovery |
|---|---|---|
| Mint fails | No file change, no redeploy | Investigate Rundeck API; run manually when fixed |
| File write fails | Backup restored, new token revoked | Same as above |
| Dockhand redeploy fails | Backup restored, new token revoked | Check Dockhand reachability; run manually |
| Verification fails | New token remains in the secrets file; old token remains valid | Investigate scope and dashboard deployment; mint a corrected replacement if needed |

When Aerie's token expires, the Automations tab degrades gracefully to the Rundeck-unreachable
state. It does not fabricate drift. An expired token is a visible-but-non-destructive outage.

## Manual fallback runbook

If the automation fails and cannot recover automatically, use the manual steps in
`myserver/rundeck/TOKEN-ROTATION.md`. That runbook covers admin-authenticated rotation from
a developer workstation.

## Monitoring

The automation writes `/opt/automations/aerie-token-rotation/results/latest.json`. Aerie
displays this on the Automations tab. The `status` field is `"success"` or `"failure"`;
`summary.value` is the expiry date of the newly minted token (YYYY-MM-DD).

A Rundeck execution failure (job error, not covered by the script's own error handling)
will leave no new `latest.json` — Aerie falls back to showing the Rundeck execution status
and the open-in-Rundeck link for logs.

## Manager token requirements

| Location | Contents | Used by |
|---|---|---|
| `/opt/dashboard-secrets/aerie-token-manager.env` | `aerie-token-manager` token | Scheduled executions |
| Rundeck secure option `manager_token` | Optional token override | Manual recovery |

The Dockhand credentials (`DOCKHAND_URL`, `DOCKHAND_USERNAME`, `DOCKHAND_PASSWORD`) are
read directly from `/opt/dashboard-secrets/dashboard-compose.env` at runtime — no additional
key storage entry needed.

Alert when `latest.json` is stale or its expiry approaches, and periodically run the job
in dry-run mode so a lost or revoked manager token is detected before rotation is due.

## Dry-run mode

Trigger the Rundeck job with option `dry_run=true` to validate manager-token loading,
inputs, and API reachability without minting tokens, editing secrets, redeploying the
dashboard, or revoking old tokens. This is the preferred validation path because it
exercises the same Rundeck execution environment as the scheduled automation.

Direct container execution is only useful if you explicitly provide the manager token:

```sh
docker exec -e RD_OPTION_MANAGER_TOKEN=<token-manager-token> \
  rundeck /bin/bash /opt/automations/current/scripts/aerie-token-rotation.sh --dry-run
```
