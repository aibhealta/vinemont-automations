# Architecture: Git-Managed Aerie Automations

Git is authoritative for Rundeck job definitions, executable scripts, and non-secret
configuration. Mutable state, results, and secrets remain outside Git-managed releases.

## Ownership and trust boundary

`aerie-automations` owns automation source and validation. `myserver/rundeck/` owns the
trusted host deployment controller, its forced-command wrapper, tests, and Rundeck mount
wiring.

The Forgejo runner sends only:

```text
deploy <40-character-lowercase-git-sha> <run-id>
rollback <40-character-lowercase-git-sha> <run-id>
verify <40-character-lowercase-git-sha>
```

It never uploads deployment code or a release archive. The host-installed controller
fetches the requested commit through a read-only Git credential, exports only `scripts/`,
`config/`, and `runbooks/rundeck/jobs/`, and rejects unsafe archive entries. Therefore a
workflow credential cannot make an uploaded executable run as root.

## Coherent deployment transaction

The host controller holds an `flock` across deploy, rollback, reconciliation,
verification, and compensation:

1. Fetch and verify the exact Git commit.
2. Export approved paths and create a canonical manifest of path, SHA-256, and executable
   bit.
3. Validate the target job UUID/name/group manifest.
4. Record the target as `pending_sha`; leave `active_sha` unchanged.
5. Atomically activate the runtime release.
6. Import/update target jobs, delete Aerie-owned UUIDs absent from the target, and verify
   the exact resulting manifest.
7. Read the release SHA from inside the already-running Rundeck container.
8. Mark the target `verified` and update `active_sha`.

If a step fails after runtime activation, the controller attempts to reactivate
`previous_sha`, reconcile its exact job manifest, and verify both surfaces. If that
compensation also fails, it records coherence as unknown and clears `active_sha` rather
than claiming a revision is active. A failed first deployment removes `current`.
`active_sha` therefore identifies only a fully verified coherent revision.

## Runtime filesystem

```text
/mnt/user/appdata/automations/
  releases/<sha>/
    scripts/
    config/
    runbooks/rundeck/jobs/
    manifest.json
    git-sha
  current -> releases/<sha>
  deployment.json
  <automation-id>/{state,results}/
```

Rundeck mounts the automation parent at `/opt/automations`. Jobs reference
`/opt/automations/current/scripts/...` and
`/opt/automations/current/config/...`, so symlink resolution occurs inside the running
container on every access. The controller makes release directories and files
root-owned/non-writable; mutable state and results remain writable outside releases.

## Jobs and rollback

Every Aerie-managed job has a permanent UUID and a group under `aerie/`. Exact
reconciliation scopes deletion to that namespace, preserving unrelated Rundeck jobs and
retaining execution history for stable UUIDs.

Rollback accepts any Git SHA still available from Forgejo. If its local release was
pruned, the controller rebuilds it from Git before activation. After an operational
rollback, revert the bad commit on `main`; otherwise the next unrelated push will deploy
it again. Retention keeps the active and previous releases plus five recent releases by
default.

## Security invariants

1. Incoming SHAs are exactly 40 lowercase hexadecimal characters.
2. Run IDs use at most 64 characters from `[A-Za-z0-9._-]`.
3. Runner-supplied executable content is never run by the privileged boundary.
4. Releases contain no symlinks, hardlinks, devices, FIFOs, traversal, or unexpected
   roots.
5. Release content is immutable to Rundeck; mutable configuration belongs in state or a
   separately designed writable store.
6. Server-side locking serializes workflow and break-glass mutations.
7. `active_sha` changes only after runtime and exact jobs verify.
8. Verification observes runtime content inside Rundeck, not only host metadata.

The Vinemont controller tests cover Git-derived deployment, strict protocol validation,
compensation, and exact reconciliation. The automation repository validator covers job
UUIDs, runtime references, syntax, executable modes, forbidden artifacts, and symlinks.
