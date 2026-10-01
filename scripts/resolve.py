#!/usr/bin/env python3
"""Map meta-server branches to builds and decide what to build or retag."""

import argparse
import configparser
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from tags import VARIANTS, fixed_tag, moving_tags

META_PATH = "holo/steamos/aarch64/vr"
USER_AGENT = os.environ.get("USER_AGENT", "holo-deckard")


def fetch(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def parse_branches(remote_info):
    cp = configparser.ConfigParser()
    cp.read_string(remote_info)
    return [b.strip() for b in cp.get("Server", "Branches").split(";") if b.strip()]


def parse_candidate(raw):
    data = json.loads(raw or "{}")
    cands = data.get("minor", {}).get("candidates", [])
    if not cands:
        return None
    c = cands[0]
    img = c["image"]
    return {
        "buildid": img["buildid"],
        "version": img["version"],
        "image_branch": img["branch"],
        "update_path": c["update_path"],
        "chunks_store_path": c["chunks_store_path"],
    }


def group(candidates):
    """{branch: candidate|None} -> builds with every branch that points at them."""
    builds = {}
    for branch, cand in candidates.items():
        if cand is None:
            continue
        b = builds.setdefault(cand["buildid"], {**cand, "branches": []})
        if b["update_path"] != cand["update_path"]:
            raise ValueError(f"build {cand['buildid']} has conflicting update paths")
        b["branches"].append(branch)
    for b in builds.values():
        b["branches"].sort()
    return [builds[k] for k in sorted(builds)]


def registry_digest(ref):
    p = subprocess.run(
        ["skopeo", "inspect", "--raw", f"docker://{ref}"], capture_output=True
    )
    if p.returncode != 0:
        return None
    return "sha256:" + hashlib.sha256(p.stdout).hexdigest()


def plan(builds, image, digest=registry_digest, force=False, only_branch=None):
    to_build, to_retag = [], []
    for b in builds:
        if only_branch and only_branch not in b["branches"]:
            continue
        if force:
            to_build.append(b)
            continue
        sources = {v: digest(f"{image}:{fixed_tag(v, b['buildid'])}") for v in VARIANTS}
        if None in sources.values():
            to_build.append(b)
            continue
        for v in VARIANTS:
            stale = [
                f"{image}:{t}"
                for t in moving_tags(v, b["branches"])
                if digest(f"{image}:{t}") != sources[v]
            ]
            if stale:
                to_retag.append(
                    {"source": f"{image}:{fixed_tag(v, b['buildid'])}", "tags": stale}
                )
    return to_build, to_retag


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--meta-url", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--branch", default="")
    p.add_argument("--force", action="store_true")
    p.add_argument("--github-output")
    a = p.parse_args(argv)

    base = f"{a.meta_url.rstrip('/')}/{META_PATH}"
    branches = parse_branches(fetch(f"{base}/remote-info.conf").decode())
    candidates = {b: parse_candidate(fetch(f"{base}/{b}.json").decode()) for b in branches}
    for b, c in candidates.items():
        print(f"{b}: {c['buildid'] if c else '(none)'}", file=sys.stderr)

    to_build, to_retag = plan(
        group(candidates), a.image, force=a.force, only_branch=a.branch or None
    )
    out = {
        "build": json.dumps(to_build),
        "retag": json.dumps(to_retag),
        "has_build": str(bool(to_build)).lower(),
        "has_retag": str(bool(to_retag)).lower(),
    }
    print(json.dumps({"build": to_build, "retag": to_retag}, indent=2))
    if a.github_output:
        with open(a.github_output, "a") as f:
            for k, v in out.items():
                f.write(f"{k}={v}\n")


if __name__ == "__main__":
    sys.exit(main())
