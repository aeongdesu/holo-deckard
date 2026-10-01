#!/usr/bin/env python3
"""Split an installed pacman root into base, base-devel and full layer file lists."""

import argparse
import fnmatch
import json
import os
import re
import sys

LAYERS = (("base", ["base"]), ("base-devel", ["base", "base-devel"]), ("full", None))
_DEP_SPLIT = re.compile(r"[<>=]")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def dep_name(dep):
    return _DEP_SPLIT.split(dep, 1)[0].strip()


def read_patterns(path):
    if not path:
        return []
    with open(path) as f:
        return [l.strip().removeprefix("./") for l in f if l.strip() and not l.startswith("#")]


class Matcher:
    """fnmatch patterns ('*' crosses '/'); a match on any ancestor matches the path."""

    def __init__(self, patterns):
        self._re = (
            re.compile("|".join(f"(?:{fnmatch.translate(p)})" for p in patterns))
            if patterns
            else None
        )

    def __call__(self, path):
        if self._re is None:
            return False
        parts = path.split("/")
        return any(self._re.match("/".join(parts[: i + 1])) for i in range(len(parts)))


def dbpath(root):
    section = None
    try:
        with open(os.path.join(root, "etc/pacman.conf")) as f:
            for line in f:
                m = _SECTION_RE.match(line)
                if m:
                    section = m.group(1)
                elif section == "options" and re.match(r"^\s*DBPath\s*=", line):
                    return line.split("=", 1)[1].strip().strip("/")
    except FileNotFoundError:
        pass
    return "var/lib/pacman"


def _read_db_file(path):
    fields, key = {}, None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("%") and line.endswith("%"):
                key = line[1:-1]
                fields[key] = []
            elif line and key:
                fields[key].append(line)
    return fields


def parse_local_db(root, db):
    local = os.path.join(root, db, "local")
    pkgs = {}
    for entry in sorted(os.listdir(local)):
        desc = os.path.join(local, entry, "desc")
        if not os.path.isfile(desc):
            continue
        d = _read_db_file(desc)
        files = os.path.join(local, entry, "files")
        f = _read_db_file(files) if os.path.isfile(files) else {}
        raw = f.get("FILES", [])
        pkgs[d["NAME"][0]] = {
            "entry": f"{db}/local/{entry}",
            "depends": d.get("DEPENDS", []),
            "provides": d.get("PROVIDES", []),
            "raw_files": raw,
            "files": [p.rstrip("/") for p in raw],
        }
    return pkgs


def closure(pkgs, roots):
    providers = {}
    for name, p in pkgs.items():
        providers.setdefault(name, set()).add(name)
        for prov in p["provides"]:
            providers.setdefault(dep_name(prov), set()).add(name)

    seen, missing, stack = set(), set(), list(roots)
    while stack:
        dep = dep_name(stack.pop())
        found = providers.get(dep)
        if not found:
            missing.add(dep)
            continue
        for name in sorted(found):
            if name not in seen:
                seen.add(name)
                stack.extend(pkgs[name]["depends"])
    if missing:
        raise SystemExit(f"unresolved dependencies for {roots}: {', '.join(sorted(missing))}")
    return seen


def walk(root):
    out = []
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        for n in dns + fns:
            out.append(os.path.normpath(os.path.join(rel, n)))
    return out


def ancestors(path):
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def split_layers(root, exclude=(), base_extra=()):
    excluded, extra = Matcher(exclude), Matcher(base_extra)
    db = dbpath(root)
    local_prefix = f"{db}/local/"
    pkgs = parse_local_db(root, db)
    owned = {f for p in pkgs.values() for f in p["files"]}

    entry_contents, db_skeleton, unowned = {}, [], []
    for p in walk(root):
        if p.startswith(local_prefix) and "/" in p[len(local_prefix):]:
            entry = local_prefix + p[len(local_prefix):].split("/", 1)[0]
            entry_contents.setdefault(entry, []).append(p)
        elif p in owned:
            continue
        elif p == db or p.startswith(db + "/"):
            db_skeleton.append(p)
        else:
            unowned.append(p)
    entries_known = {p["entry"] for p in pkgs.values()}
    db_skeleton = [p for p in db_skeleton if p not in entries_known]

    emitted, prev, layers = set(), set(), {}
    for name, roots in LAYERS:
        members = closure(pkgs, roots) if roots else set(pkgs)
        new = members - prev
        prev = members
        cand = []
        for pkg in sorted(new):
            entry = pkgs[pkg]["entry"]
            cand += pkgs[pkg]["files"]
            cand.append(entry)
            cand += entry_contents.get(entry, [])
        if name == "base":
            cand += db_skeleton
            cand += [p for p in unowned if extra(p)]
        if roots is None:
            cand += unowned

        entries = set()
        for p in cand:
            if p in emitted or p in entries:
                continue
            if excluded(p) or not os.path.lexists(os.path.join(root, p)):
                continue
            entries.add(p)
            for a in ancestors(p):
                if a not in emitted:
                    entries.add(a)
        emitted |= entries
        layers[name] = {"packages": sorted(new), "members": members, "entries": sorted(entries)}

    for layer in layers.values():
        layer["missing"] = sorted(
            f"{pkg} /{raw}"
            for pkg in layer.pop("members")
            for raw in pkgs[pkg]["raw_files"]
            if raw.rstrip("/") not in emitted
        )
    return layers


def size_of(root, entries):
    total = 0
    for p in entries:
        st = os.lstat(os.path.join(root, p))
        if not os.path.isdir(os.path.join(root, p)) or os.path.islink(os.path.join(root, p)):
            total += st.st_size
    return total


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True)
    p.add_argument("--exclude")
    p.add_argument("--base-extra")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)

    layers = split_layers(a.root, read_patterns(a.exclude), read_patterns(a.base_extra))
    os.makedirs(a.out, exist_ok=True)
    summary = {}
    for name, layer in layers.items():
        with open(os.path.join(a.out, f"{name}.list"), "wb") as f:
            for e in layer["entries"]:
                f.write(e.encode() + b"\0")
        with open(os.path.join(a.out, f"{name}.missing"), "w") as f:
            f.writelines(m + "\n" for m in layer["missing"])
        summary[name] = {
            "packages": len(layer["packages"]),
            "entries": len(layer["entries"]),
            "bytes": size_of(a.root, layer["entries"]),
        }
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())
