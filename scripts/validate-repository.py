#!/usr/bin/env python3
import glob
import json
import os
import re
import subprocess
import sys

try:
    import yaml
except ImportError:
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
errors = []


def relative(path):
    return os.path.relpath(path, ROOT)


for path in glob.glob(f"{ROOT}/scripts/**/*.sh", recursive=True):
    result = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
    if result.returncode:
        errors.append(f"{relative(path)}: {result.stderr.strip()}")

for path in glob.glob(f"{ROOT}/scripts/**/*", recursive=True):
    if os.path.isfile(path) and not os.path.islink(path):
        if path.endswith((".sh", ".py")) and not os.access(path, os.X_OK):
            errors.append(f"{relative(path)}: executable entrypoint lacks executable mode")

for path in glob.glob(f"{ROOT}/scripts/**/*.py", recursive=True):
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", path],
        env={**os.environ, "PYTHONPYCACHEPREFIX": "/tmp/aerie-validation-pycache"},
        capture_output=True,
        text=True,
    )
    if result.returncode:
        errors.append(f"{relative(path)}: {result.stderr.strip()}")

for path in glob.glob(f"{ROOT}/config/**/*.json", recursive=True):
    try:
        with open(path, encoding="utf-8") as handle:
            json.load(handle)
    except Exception as exc:
        errors.append(f"{relative(path)}: invalid JSON: {exc}")

job_paths = sorted(
    glob.glob(f"{ROOT}/runbooks/rundeck/jobs/**/*.yaml", recursive=True)
    + glob.glob(f"{ROOT}/runbooks/rundeck/jobs/**/*.yml", recursive=True)
)
required = ("uuid", "name", "group", "nodefilters", "scheduleEnabled", "sequence")
seen_uuids = {}

for path in job_paths:
    try:
        if yaml is not None:
            with open(path, encoding="utf-8") as handle:
                jobs = yaml.safe_load(handle)
        else:
            parsed = subprocess.run(
                [
                    "ruby",
                    "-ryaml",
                    "-rjson",
                    "-e",
                    "print JSON.generate(YAML.safe_load(File.read(ARGV[0]), aliases: false))",
                    path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            jobs = json.loads(parsed.stdout)
    except Exception as exc:
        errors.append(f"{relative(path)}: invalid YAML: {exc}")
        continue
    if not isinstance(jobs, list):
        errors.append(f"{relative(path)}: top-level value must be a list")
        continue
    for job in jobs:
        if not isinstance(job, dict):
            errors.append(f"{relative(path)}: each job must be a mapping")
            continue
        name = job.get("name", "?")
        for field in required:
            if field not in job:
                errors.append(f"{relative(path)}/{name}: missing {field}")
        if not job.get("nodefilters", {}).get("filter", "").strip():
            errors.append(f"{relative(path)}/{name}: nodefilters.filter must be explicit")
        uuid = str(job.get("uuid", ""))
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            uuid,
        ):
            errors.append(f"{relative(path)}/{name}: uuid must be a canonical lowercase UUID")
        elif uuid in seen_uuids:
            errors.append(
                f"{relative(path)}/{name}: duplicate uuid also used by {seen_uuids[uuid]}"
            )
        else:
            seen_uuids[uuid] = f"{relative(path)}/{name}"
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    for match in re.finditer(
        r"/opt/automations/current/(scripts|config)/([A-Za-z0-9._/-]+)", text
    ):
        repository_path = os.path.join(ROOT, match.group(1), match.group(2).rstrip(".,"))
        if not os.path.isfile(repository_path):
            errors.append(
                f"{relative(path)}: referenced runtime file is missing: "
                f"{relative(repository_path)}"
            )
    for match in re.finditer(r"/opt/automations/(scripts|config)/", text):
        errors.append(
            f"{relative(path)}: runtime references must resolve through /opt/automations/current/"
        )

for tree in ("scripts", "config"):
    for directory, names, files in os.walk(os.path.join(ROOT, tree)):
        for name in names + files:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                errors.append(f"{relative(path)}: symlinks are forbidden")
            if name == "__pycache__" or name.endswith((".pyc", ".pyo", ".swp", "~")):
                errors.append(f"{relative(path)}: development artifact is forbidden")

if errors:
    for error in errors:
        print(f"ERROR: {error}")
    sys.exit(1)

print(f"Repository validation passed ({len(job_paths)} Rundeck job files).")
