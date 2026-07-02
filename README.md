# aerie-automations

Git source of truth for Rundeck-managed Aerie automations.

Aerie (the homelab dashboard) displays and lightly controls automations through Rundeck's
API. This repo holds the automation definitions and scripts. Deployment wiring (the Rundeck
container and compose) lives in `myserver/rundeck/`.

## Layout

```
runbooks/rundeck/jobs/   — Rundeck job YAML reconciled by the host controller
scripts/                 — Shell scripts executed by Rundeck job steps
config/                  — Non-secret runtime configuration
docs/                    — Operations runbooks
  architecture.md        — Deployment architecture, boundaries, and invariants
  operations.md          — Initial setup and how to add new automations
  token-rotation.md      — How the token rotation automation works
.forgejo/workflows/
  validate.yml           — pull-request validation
  deploy.yml             — validation and coherent push-to-main deployment
```

## Automations

| Job | Group | Schedule | What it does |
|-----|-------|----------|--------------|
| `aerie-token-rotation` | `aerie/ops` | 1st & 22nd of month, 03:00 UTC | Rotates Aerie's Rundeck API token before the 30-day expiry |
| `job-scout` | `aerie/personal` | 08:00, 12:00, and 16:00 UTC | Finds matching jobs from configured company boards |

## Quick reference

- See `AGENTS.md` for conventions, secret locations, and what NOT to do here.
- See `docs/architecture.md` for the deployment architecture and security model.
- See `docs/operations.md` for deployment, rollback, and key storage.
- See `docs/token-rotation.md` for how the token rotation automation works and failure recovery.
- Manual fallback for token rotation: `myserver/rundeck/TOKEN-ROTATION.md`.
