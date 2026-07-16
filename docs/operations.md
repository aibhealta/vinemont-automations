# Operations Guide — aerie-automations

Forgejo `aerie-automations/main` is the source of truth for Rundeck job definitions,
scripts, non-secret configuration, and documentation. Ordinary deployment requires only
a reviewed push to `main`; do not copy files to Vinemont manually.

## Deployment flow

1. Forgejo Actions checks out the exact pushed commit.
2. `scripts/validate-repository.py` validates shell/Python/JSON/YAML, executable modes,
   job-required fields, runtime references, forbidden artifacts, and symlinks.
3. The workflow requests deployment of the exact SHA through the restricted Vinemont
   deployment account; it uploads no executable content.
4. Vinemont's trusted controller fetches that commit, stages a read-only release, and
   atomically repoints `current`.
5. The controller reconciles the exact Aerie job UUID manifest and deletes stale
   Aerie-owned jobs.
6. It verifies the SHA from inside the running Rundeck container before changing
   `active_sha`.

Runtime layout:

```text
/mnt/user/appdata/automations/
  releases/<git-sha>/{scripts,config}/
  current -> releases/<git-sha>
  deployment.json
  <automation-id>/{state,results}/
```

Rundeck resolves `/opt/automations/current/scripts` and
`/opt/automations/current/config` inside the running container. Root-owned release modes
make them non-writable. Per-automation state/results remain writable outside releases.

## One-time Forgejo and server setup

Install Vinemont's `rundeck/aerie-deploy-command.sh` and
`rundeck/aerie-deploy-controller.py` under `/boot/config/aerie-automations/`. Create a
bare read-only clone at `/mnt/user/appdata/automations-repository.git` and a deployment
environment file containing the Rundeck URL/token. Create a dedicated SSH key whose
`authorized_keys` entry forces that command and disables forwarding, PTY allocation,
and user commands. Unraid permits SSH only as root, but this credential cannot obtain a
root shell; the wrapper permits only deploy, rollback, and verification requests. The
trusted controller—not the requested commit—performs privileged work.

Configure these Forgejo Actions secrets:

| Secret | Purpose |
|---|---|
| `AERIE_DEPLOY_HOST` | Vinemont SSH host |
| `AERIE_DEPLOY_SSH_KEY` | Dedicated private key |

Install Ruby on Vinemont; the trusted controller uses its standard YAML parser.

Deploy the accompanying `myserver/rundeck/docker-compose.yml` change through the normal
Vinemont Git/Dockhand path before the first automated runtime deployment.

Before that redeploy, move the two token-rotation files into their narrow secret directory:

```sh
mkdir -p /mnt/user/appdata/docker-secrets/dashboard
mv /mnt/user/appdata/docker-secrets/dashboard-compose.env \
  /mnt/user/appdata/docker-secrets/dashboard/dashboard-compose.env
mv /mnt/user/appdata/docker-secrets/aerie-token-manager.env \
  /mnt/user/appdata/docker-secrets/dashboard/aerie-token-manager.env
```

The dashboard compose `.env` symlink and Rundeck mount both target this directory.

## Inspect and retry

Read `/mnt/user/appdata/automations/deployment.json` for `active_sha`, `pending_sha`,
`previous_sha`, status, workflow run ID, errors, and compensation outcome.
Fix the cause and rerun the failed Forgejo workflow.

## Rollback

Run the `deploy` workflow manually with `rollback_sha` set to a full Git SHA. The
controller rebuilds a pruned release from Git if needed, then atomically restores runtime
and the exact job set. Mutable state/results are not rolled back. Follow with a Git revert
on `main`.

## Adding an automation

1. Add job YAML under `runbooks/rundeck/jobs/aerie/<category>/` using group
   `aerie/<category>` and explicit `name: Vinemont` node filtering.
2. Add executable entrypoints under `scripts/` and non-secret settings under `config/`.
3. Reference repository files through `/opt/automations/current/scripts/` or
   `/opt/automations/current/config/`.
4. Add the automation to `dashboard/automations.json`.
5. Push all owning-repo changes. The automation repo deploys runtime/job content;
   dashboard and Vinemont changes deploy through their own repositories.

## Break-glass recovery

If Forgejo Actions and rollback are both unavailable, an operator may repoint `current`
to an intact release on Vinemont. Record the incident, restore the workflow, and
immediately reconcile the active SHA through a workflow rollback. Never edit files inside
a release or treat manual copying as steady-state deployment.
