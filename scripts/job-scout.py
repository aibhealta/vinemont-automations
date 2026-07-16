#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request


CONFIG_SCHEMA = "aerie.job-scout.config.v1"
RESULT_SCHEMA = "aerie.automation.result.v1"
USER_AGENT = "aerie-job-scout/1.0"
NORMALIZED_FIELDS = {
    "source",
    "company",
    "job_id",
    "title",
    "department",
    "location",
    "work_mode",
    "employment_type",
    "salary_min",
    "salary_max",
    "salary_currency",
    "required_experience_years",
    "posted_at",
    "updated_at",
    "first_found_at",
    "url",
    "description",
}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_timestamp(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        # Lever represents createdAt as Unix milliseconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def infer_work_mode(title, location, description):
    haystack = " ".join([title or "", location or "", description or ""]).lower()
    if "remote" in haystack:
        return "remote"
    if "hybrid" in haystack:
        return "hybrid"
    if location:
        return "onsite"
    return "unknown"


def infer_employment_type(title, description):
    haystack = " ".join([title or "", description or ""]).lower()
    if re.search(r"\b(intern|internship)\b", haystack):
        return "internship"
    if "contract" in haystack or "contractor" in haystack:
        return "contract"
    if "part time" in haystack or "part-time" in haystack:
        return "part_time"
    if "temporary" in haystack or "temp " in haystack:
        return "temporary"
    return "full_time"


def normalize_salary(value):
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 1000:
        return round(amount * 2080)
    return round(amount)


def extract_salary(text):
    if not text:
        return None, None, None
    compact = text.replace(",", "")
    pattern = re.compile(r"\$?\b(\d{2,3}(?:\.\d+)?)\s?k?\s*(?:-|to|–)\s*\$?\s*(\d{2,3}(?:\.\d+)?)\s?k\b", re.I)
    match = pattern.search(compact)
    if not match:
        return None, None, None
    low, high = float(match.group(1)), float(match.group(2))
    if "k" in match.group(0).lower() or high < 1000:
        low *= 1000
        high *= 1000
    return round(low), round(high), "USD"


def extract_required_experience_years(text):
    if not text:
        return None
    patterns = [
        re.compile(
            r"\b(\d{1,2})\s*\+?\s*years?\s+(?:of\s+)?"
            r"(?:relevant\s+|professional\s+|work\s+|product(?:\s+management)?\s+)?experience\b",
            re.I,
        ),
        re.compile(r"\b(?:minimum(?:\s+of)?|at\s+least)\s+(\d{1,2})\s+years?\b", re.I),
    ]
    years = []
    for pattern in patterns:
        years.extend(int(match.group(1)) for match in pattern.finditer(text))
    return max(years) if years else None


def stable_key(posting):
    raw = "|".join([posting.get("source", ""), posting.get("company", ""), posting.get("job_id", ""), posting.get("url", "")])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalized_posting(**kwargs):
    posting = {field: kwargs.get(field) for field in NORMALIZED_FIELDS}
    posting["title"] = clean_text(posting.get("title"))
    posting["company"] = clean_text(posting.get("company"))
    posting["department"] = clean_text(posting.get("department"))
    posting["location"] = clean_text(posting.get("location"))
    posting["description"] = strip_html(posting.get("description"))
    posting["url"] = clean_text(posting.get("url"))
    posting["work_mode"] = posting.get("work_mode") or infer_work_mode(posting["title"], posting["location"], posting["description"])
    posting["employment_type"] = posting.get("employment_type") or infer_employment_type(posting["title"], posting["description"])
    posting["required_experience_years"] = (
        posting.get("required_experience_years")
        or extract_required_experience_years(posting["description"])
    )
    if posting.get("salary_min") is None and posting.get("salary_max") is None:
        low, high, currency = extract_salary(posting["description"])
        posting["salary_min"] = low
        posting["salary_max"] = high
        posting["salary_currency"] = currency
    posting["salary_min"] = normalize_salary(posting.get("salary_min"))
    posting["salary_max"] = normalize_salary(posting.get("salary_max"))
    posting["posted_at"] = normalize_timestamp(posting.get("posted_at"))
    posting["updated_at"] = normalize_timestamp(posting.get("updated_at"))
    posting["key"] = stable_key(posting)
    return posting


def fetch_greenhouse(company):
    board = company["board"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(board)}/jobs?content=true"
    data = fetch_json(url)
    postings = []
    for job in data.get("jobs", []):
        departments = job.get("departments") or []
        department = departments[0].get("name") if departments else None
        location = (job.get("location") or {}).get("name")
        postings.append(normalized_posting(
            source="greenhouse",
            company=company["name"],
            job_id=str(job.get("id") or ""),
            title=job.get("title"),
            department=department,
            location=location,
            updated_at=job.get("updated_at"),
            url=job.get("absolute_url"),
            description=job.get("content"),
        ))
    return postings


def fetch_lever(company):
    board = company["board"]
    url = f"https://api.lever.co/v0/postings/{urllib.parse.quote(board)}?mode=json"
    data = fetch_json(url)
    postings = []
    for job in data:
        categories = job.get("categories") or {}
        location = categories.get("location")
        commitment = categories.get("commitment")
        postings.append(normalized_posting(
            source="lever",
            company=company["name"],
            job_id=str(job.get("id") or ""),
            title=job.get("text"),
            department=categories.get("team"),
            location=location,
            employment_type=(commitment or "").lower().replace("-", "_") or None,
            posted_at=job.get("createdAt"),
            url=job.get("hostedUrl") or job.get("applyUrl"),
            description=job.get("descriptionPlain") or job.get("description"),
        ))
    return postings


def fetch_ashby(company):
    board = company["board"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(board)}"
    data = fetch_json(url)
    jobs = data.get("jobs", data if isinstance(data, list) else [])
    postings = []
    for job in jobs:
        location = job.get("locationName") or job.get("location")
        postings.append(normalized_posting(
            source="ashby",
            company=company["name"],
            job_id=str(job.get("id") or job.get("jobId") or ""),
            title=job.get("title"),
            department=job.get("department") or job.get("team"),
            location=location,
            employment_type=job.get("employmentType"),
            posted_at=job.get("publishedDate"),
            updated_at=job.get("updatedAt"),
            url=job.get("jobUrl") or job.get("applicationUrl"),
            description=job.get("descriptionHtml") or job.get("descriptionPlain") or job.get("description"),
        ))
    return postings


def fetch_json_feed(company):
    data = fetch_json(company["url"])
    jobs = data
    for key in company.get("jobs_path", []):
        jobs = jobs.get(key, [])
    mapping = company.get("field_map", {})
    postings = []
    for job in jobs:
        kwargs = {"source": "json", "company": company["name"]}
        for normalized, raw in mapping.items():
            kwargs[normalized] = job.get(raw)
        postings.append(normalized_posting(**kwargs))
    return postings


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "json": fetch_json_feed,
}


def compare(actual, op, expected):
    if op in {"exists", "missing"}:
        exists = actual not in (None, "", [])
        return exists if op == "exists" else not exists
    if actual is None:
        return op.endswith("_or_missing")
    if op in {"eq", "ne"}:
        ok = str(actual).lower() == str(expected).lower()
        return ok if op == "eq" else not ok
    if op in {"contains", "not_contains"}:
        ok = str(expected).lower() in str(actual).lower()
        return ok if op == "contains" else not ok
    if op == "any_contains":
        return any(str(v).lower() in str(actual).lower() for v in expected)
    if op == "not_any_contains":
        return not any(str(v).lower() in str(actual).lower() for v in expected)
    if op == "in":
        return str(actual).lower() in {str(v).lower() for v in expected}
    if op == "not_in":
        return str(actual).lower() not in {str(v).lower() for v in expected}
    if op in {"gt", "gt_or_missing", "gte", "gte_or_missing", "lt", "lt_or_missing", "lte", "lte_or_missing"}:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        if op.startswith("gt_") or op == "gt":
            return left > right
        if op.startswith("gte"):
            return left >= right
        if op.startswith("lt_") or op == "lt":
            return left < right
        return left <= right
    raise ValueError(f"unknown criteria op: {op}")


def rule_matches(posting, rule):
    if "all" in rule:
        child_results = [rule_matches(posting, child) for child in rule.get("all", [])]
        return bool(child_results) and all(child_results)

    field = rule.get("field")
    if field not in NORMALIZED_FIELDS:
        raise ValueError(f"criteria field {field!r} is not a normalized posting field")
    return compare(posting.get(field), rule.get("op"), rule.get("value"))


def matches_criteria(posting, criteria):
    for rule in criteria.get("filters", []):
        if not rule_matches(posting, rule):
            return False, 0, [f"filtered: {rule.get('field')} {rule.get('op')}"]

    for group in criteria.get("reject_if", []):
        if rule_matches(posting, {"all": group.get("all", [])}):
            return False, 0, [f"filtered: {group.get('label') or 'reject_if'}"]

    score = 0
    reasons = []
    for rule in criteria.get("scoring", []):
        if rule_matches(posting, rule):
            points = int(rule.get("score", 0))
            score += points
            reasons.append(f"{rule.get('label') or rule.get('field')}: {points:+d}")

    for group in criteria.get("scoring_groups", []):
        matches = []
        for rule in group.get("rules", []):
            if rule_matches(posting, rule):
                matches.append(rule)
        if matches:
            rule = max(matches, key=lambda item: int(item.get("score", 0)))
            points = int(rule.get("score", 0))
            score += points
            reasons.append(f"{rule.get('label') or group.get('label') or 'scoring group'}: {points:+d}")

    return score >= int(criteria.get("minimum_score", 0)), score, reasons


def summarize_posting(posting):
    def cell(value):
        if value is None:
            return ""
        return str(value)

    return {
        "highlight": "yes" if posting.get("highlight") else "",
        "company": cell(posting["company"]),
        "title": cell(posting["title"]),
        "location": cell(posting["location"]),
        "work_mode": cell(posting["work_mode"]),
        "employment_type": cell(posting["employment_type"]),
        "salary_min": cell(posting["salary_min"]),
        "salary_max": cell(posting["salary_max"]),
        "posted_at": cell(posting["posted_at"]),
        "first_found_at": cell(posting["first_found_at"]),
        "score": cell(posting.get("match_score")),
        "reasons": "; ".join(posting.get("match_reasons", [])),
        "url": cell(posting["url"]),
    }


def result(status, summary_value, blocks, errors=None, execution_id=None):
    payload = {
        "schema": RESULT_SCHEMA,
        "automation_id": "job-scout",
        "execution_id": execution_id or os.environ.get("RD_JOB_EXECID") or os.environ.get("RD_JOB_EXECUTIONID") or "manual",
        "status": status,
        "completed_at": utc_now(),
        "summary": {"label": "New matches", "value": str(summary_value)},
        "blocks": blocks,
    }
    if errors:
        payload["error"] = {"message": "; ".join(errors)}
    return payload


def run(args):
    config = read_json(args.config)
    if not config:
        raise ValueError(f"missing config: {args.config}")
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"unsupported config schema: {config.get('schema')}")

    previous = read_json(args.state, default={"seen": {}})
    seen = previous.get("seen", {})
    current_seen = {}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    retention_days = int(config.get("state_retention_days", 30))
    retention_cutoff = now - dt.timedelta(days=retention_days)
    for key, entry in seen.items():
        last_seen = parse_time(entry.get("last_seen_at"))
        if last_seen and last_seen >= retention_cutoff:
            current_seen[key] = entry
    all_postings = []
    filtered_postings = []
    errors = []

    for company in config.get("companies", []):
        if not company.get("enabled", True):
            continue
        source = company.get("source")
        fetcher = FETCHERS.get(source)
        if not fetcher:
            errors.append(f"{company.get('name', '?')}: unsupported source {source!r}")
            continue
        try:
            postings = fetcher(company)
            all_postings.extend(postings)
        except Exception as exc:
            errors.append(f"{company.get('name', '?')}: {exc}")

    matching = []
    for posting in all_postings:
        previous_entry = seen.get(posting["key"], {})
        posting["first_found_at"] = previous_entry.get("first_found_at") or now.isoformat().replace("+00:00", "Z")
        is_match, score, reasons = matches_criteria(posting, config.get("criteria", {}))
        posting["match_score"] = score
        posting["match_reasons"] = reasons
        posting["highlight"] = score >= int(config.get("criteria", {}).get("highlight_score", 999999))
        if is_match:
            matching.append(posting)
        else:
            filtered_postings.append(posting)
    for posting in all_postings:
        current_seen[posting["key"]] = {
            "company": posting["company"],
            "title": posting["title"],
            "url": posting["url"],
            "first_found_at": posting["first_found_at"],
            "last_seen_at": now.isoformat().replace("+00:00", "Z"),
        }

    new_matches = [p for p in matching if p["key"] not in seen]
    existing_matches = [p for p in matching if p["key"] in seen]
    write_json_atomic(args.state, {"schema": "aerie.job-scout.state.v1", "updated_at": utc_now(), "seen": current_seen})

    blocks = [
        {
            "type": "metrics",
            "title": "Run summary",
            "metrics": [
                {"label": "companies checked", "value": str(len([c for c in config.get("companies", []) if c.get("enabled", True)]))},
                {"label": "postings fetched", "value": str(len(all_postings))},
                {"label": "matching postings", "value": str(len(matching))},
                {"label": "new matching postings", "value": str(len(new_matches))},
                {"label": "existing matching postings", "value": str(len(existing_matches))},
            ],
        }
    ]
    if new_matches:
        blocks.append({
            "type": "table",
            "title": "New matches",
            "columns": ["highlight", "score", "company", "title", "location", "work_mode", "employment_type", "salary_min", "salary_max", "posted_at", "first_found_at", "reasons", "url"],
            "rows": [summarize_posting(p) for p in sorted(new_matches, key=lambda p: p.get("match_score", 0), reverse=True)],
        })
    if existing_matches:
        existing_limit = int(config.get("existing_matches_limit", 25))
        blocks.append({
            "type": "table",
            "title": f"Top existing matches ({existing_limit})",
            "columns": ["highlight", "score", "company", "title", "location", "work_mode", "employment_type", "salary_min", "salary_max", "posted_at", "first_found_at", "reasons", "url"],
            "rows": [summarize_posting(p) for p in sorted(existing_matches, key=lambda p: p.get("match_score", 0), reverse=True)[:existing_limit]],
        })
    if errors:
        blocks.append({"type": "message", "title": "Fetch errors", "text": "\n".join(errors)})

    debug_filtered_path = config.get("debug_filtered_path")
    if debug_filtered_path:
        write_json_atomic(debug_filtered_path, {
            "schema": "aerie.job-scout.filtered-debug.v1",
            "finished_at": utc_now(),
            "count": len(filtered_postings),
            "postings": [summarize_posting(p) for p in sorted(filtered_postings, key=lambda p: p.get("match_score", 0), reverse=True)],
        })

    status = "failure" if errors and not all_postings else "success"
    write_json_atomic(args.result, result(status, len(new_matches), blocks, errors if status == "failure" else None, args.execution_id))
    return 1 if status == "failure" else 0


def main():
    parser = argparse.ArgumentParser(description="Check configured company job boards for new matching postings.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--execution-id")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        write_json_atomic(args.result, result("failure", 0, [{"type": "message", "title": "Error", "text": str(exc)}], [str(exc)], args.execution_id))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
