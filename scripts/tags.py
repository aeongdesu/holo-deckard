#!/usr/bin/env python3
"""Tag rules shared by the build and retag paths."""

import argparse
import re
import sys

VARIANTS = ("base", "base-devel", "full")
DEFAULT_VARIANT = "base"
DEFAULT_BRANCH = "stable"
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _check(tag):
    if not _TAG_RE.match(tag):
        raise ValueError(f"invalid tag: {tag!r}")
    return tag


def moving_tags(variant, branches):
    branches = sorted(set(branches))
    tags = [f"{variant}-{b}" for b in branches]
    if DEFAULT_BRANCH in branches:
        tags.append(variant)
        if variant == DEFAULT_VARIANT:
            tags.append("latest")
    return [_check(t) for t in tags]


def fixed_tag(variant, buildid):
    return _check(f"{variant}-{buildid}")


def all_tags(variant, buildid, branches):
    """Moving tags first, the fixed tag last (its presence marks a finished push)."""
    return moving_tags(variant, branches) + [fixed_tag(variant, buildid)]


def bake_var(variant):
    return variant.upper().replace("-", "_") + "_TAGS"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("bake", "list"):
        s = sub.add_parser(name)
        s.add_argument("--image", required=True)
        s.add_argument("--buildid", required=True)
        s.add_argument("--branches", required=True, help="comma-separated")
        if name == "list":
            s.add_argument("--variant", required=True, choices=VARIANTS)
    a = p.parse_args(argv)
    branches = [b for b in a.branches.split(",") if b]

    if a.cmd == "bake":
        for v in VARIANTS:
            refs = [f"{a.image}:{t}" for t in all_tags(v, a.buildid, branches)]
            print(f"{bake_var(v)}={','.join(refs)}")
    else:
        for t in all_tags(a.variant, a.buildid, branches):
            print(f"{a.image}:{t}")


if __name__ == "__main__":
    sys.exit(main())
