# Job Scout

`job-scout` checks company career boards three times per day and reports new
postings that match configured criteria.

## Runtime files

The script expects these files inside the Rundeck container:

| Path | Purpose |
|---|---|
| `/opt/automations/current/scripts/job-scout.sh` | Rundeck entrypoint |
| `/opt/automations/current/scripts/job-scout.py` | Fetch, normalize, match, and write results |
| `/opt/automations/current/config/job-scout.json` | Company list and field-based criteria |
| `/opt/automations/job-scout/state/postings.json` | Seen posting keys |
| `/opt/automations/job-scout/results/latest.json` | Aerie result artifact |

`job-scout` requires `python3` in the Rundeck execution environment. The
current job runs as an in-container script step, so the Rundeck container image
must include Python for this automation to survive redeploys.

Seen postings are retained for `state_retention_days` from the config. The
default is 30 days. State records preserve `first_found_at`, the first time Job
Scout observed a posting. Existing state created before this field was added is
backfilled with the first run after upgrade.

New matches are shown in full. Existing matches are shown separately as a
tuning aid, capped by `existing_matches_limit`.

Set `debug_filtered_path` to a writable JSON path to inspect postings that did
not pass filters or score threshold. Leave it `null` during normal operation so
the dashboard artifact stays small.

## Company config

The company list is data, not code. Add entries under `companies`:

```json
{
  "name": "Acme",
  "enabled": true,
  "source": "greenhouse",
  "board": "acme"
}
```

Supported `source` values:

| Source | Required fields | Notes |
|---|---|---|
| `greenhouse` | `name`, `board` | Uses the public Greenhouse job board API with full content. |
| `lever` | `name`, `board` | Uses Lever's public postings API. |
| `ashby` | `name`, `board` | Uses Ashby's public job board posting API. |
| `json` | `name`, `url`, `field_map` | For simple custom JSON feeds. |

Keep disabled examples in the config as templates, but set real companies to
`"enabled": true`.

## Normalized posting fields

Criteria rules must use one of these fields:

- `source`
- `company`
- `job_id`
- `title`
- `department`
- `location`
- `work_mode`
- `employment_type`
- `salary_min`
- `salary_max`
- `salary_currency`
- `required_experience_years`
- `posted_at`
- `updated_at`
- `first_found_at`
- `url`
- `description`

The current script infers `work_mode`, `employment_type`, and basic USD salary
ranges when the upstream job board does not provide them directly. It also
extracts the highest explicit minimum-years requirement from common phrases in
the description. `posted_at` comes from the upstream board when available;
`first_found_at` is assigned and persisted by Job Scout. Unknown values remain
`null` or `unknown`.

## Criteria rules

Criteria use a hybrid model:

- `filters` are hard yes/no disqualifiers.
- `reject_if` groups are compound hard disqualifiers; every rule in a group's
  `all` list must match for the posting to be rejected.
- `scoring` rules add or subtract points.
- `scoring_groups` add or subtract points from only the highest-scoring matched
  rule in each group. Use this for mutually exclusive dimensions such as
  location preference, where a posting should not receive credit for every
  location it lists.
- `minimum_score` decides whether a posting is shown.

```json
{
  "criteria": {
    "minimum_score": 700,
    "highlight_score": 1800,
    "filters": [
      { "field": "title", "op": "not_any_contains", "value": ["software engineer"] }
    ],
    "reject_if": [
      {
        "label": "hybrid outside preferred hubs",
        "all": [
          { "field": "work_mode", "op": "eq", "value": "hybrid" },
          { "field": "location", "op": "not_any_contains", "value": ["san francisco", "new york"] }
        ]
      }
    ],
    "scoring": [
      { "label": "product role", "field": "title", "op": "contains", "value": "product", "score": 700 }
    ],
    "scoring_groups": [
      {
        "label": "location preference",
        "rules": [
          { "label": "San Francisco", "field": "location", "op": "contains", "value": "san francisco", "score": 1000 },
          { "label": "New York", "field": "location", "op": "contains", "value": "new york", "score": 600 },
          { "label": "remote", "field": "work_mode", "op": "eq", "value": "remote", "score": 400 },
          {
            "label": "onsite outside hubs",
            "all": [
              { "field": "work_mode", "op": "eq", "value": "onsite" },
              { "field": "location", "op": "not_any_contains", "value": ["san francisco", "new york"] }
            ],
            "score": -700
          }
        ]
      }
    ]
  }
}
```

Supported operators:

- `exists`
- `missing`
- `eq`
- `ne`
- `contains`
- `not_contains`
- `any_contains`
- `not_any_contains`
- `in`
- `not_in`
- `gt`
- `gt_or_missing`
- `gte`
- `gte_or_missing`
- `lt`
- `lt_or_missing`
- `lte`
- `lte_or_missing`

Use `gte_or_missing` and `lte_or_missing` for salary if missing salary should
not exclude otherwise promising postings.

## Deployment

Commit the script, config, documentation, and
`runbooks/rundeck/jobs/aerie/personal/job-scout.yaml` together and push to `main`.
The validated deployment workflow atomically activates the runtime files and imports
the Rundeck job. Mutable state and results remain outside Git-managed releases. The
dashboard already allows the `job-scout` automation id.
