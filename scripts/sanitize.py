#!/usr/bin/env python3
"""Strip credentials and tokenized repository URLs from a SteamOS root filesystem."""

import argparse
import os
import re
import sys

TOKEN_RE = re.compile(r"_([0-9a-f]{64})_DO_NOT_SHARE_URL")
TOKEN_URL_RE = re.compile(r"(https?://[^/\s]+/[A-Za-z0-9.-]+)_[0-9a-f]{64}_DO_NOT_SHARE_URL")
USERINFO_RE = re.compile(r"(https?://)([^/@\s:]+):([^/@\s]+)@")
CRED_RE = re.compile(r"^\s*(username|password)\s*=\s*(.*?)\s*$", re.I)
SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
MAX_TEXT_SIZE = 1 << 20
ERE_SPECIAL = set(".[]{}()\\*+?^$|")


def _read_text(path):
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    if os.path.getsize(path) > MAX_TEXT_SIZE:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, PermissionError):
        return None


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _etc_files(root):
    for dp, _, fns in os.walk(os.path.join(root, "etc")):
        for n in fns:
            yield os.path.join(dp, n)


def collect_secrets(root):
    """Exact secret values present in the original image, for the leak gate."""
    found = set()
    for path in _etc_files(root):
        text = _read_text(path)
        if text is None:
            continue
        found.update(TOKEN_RE.findall(text))
        for _, user, pw in USERINFO_RE.findall(text):
            found.update((user, pw))
        if "/steamos-atomupd/" in path:
            for line in text.splitlines():
                m = CRED_RE.match(line)
                if m and m.group(2):
                    found.add(m.group(2))
    return sorted(v for v in found if len(v) >= 8)


def ere_escape(s):
    return "".join("\\" + c if c in ERE_SPECIAL else c for c in s)


def rewrite_pacman_conf(text):
    """Point token URLs at their public paths and mark those repos unsigned."""
    out = []
    sections = [[]]
    for line in text.splitlines(keepends=True):
        if SECTION_RE.match(line):
            sections.append([line])
        else:
            sections[-1].append(line)

    for lines in sections:
        rewritten = False
        new = []
        for line in lines:
            if re.match(r"^\s*Server\s*=", line) and TOKEN_RE.search(line):
                line = TOKEN_URL_RE.sub(r"\1", line)
                rewritten = True
            new.append(line)
        if rewritten and not any(re.match(r"^\s*SigLevel\s*=", l) for l in new):
            new.insert(1, "SigLevel = Optional\n")
        out.extend(new)

    result = "".join(out)
    if TOKEN_RE.search(result):
        raise ValueError("pacman.conf still contains a private token")
    return result


def strip_credentials(text):
    lines = [l for l in text.splitlines(keepends=True) if not CRED_RE.match(l)]
    return "".join(lines)


BIN_USERINFO_RE = re.compile(rb"https?://([^/@:\s]+:[^/@\s]+@)[^/\s]*steamos\.cloud")
BIN_TOKEN_RE = re.compile(rb"steamos\.cloud/[A-Za-z0-9.-]+_([0-9a-f]{64})_DO_NOT_SHARE_URL")
SCRUB_SKIP = ("proc", "sys", "dev", "run", "tmp")


def scrub_file(path):
    """Overwrite embedded credentials with same-length filler; returns the number replaced."""
    with open(path, "rb") as f:
        data = f.read()
    if b"steamos.cloud" not in data:
        return 0
    spans = [m.span(1) for r in (BIN_USERINFO_RE, BIN_TOKEN_RE) for m in r.finditer(data)]
    if not spans:
        return 0
    buf = bytearray(data)
    for start, end in spans:
        buf[start:end] = b"x" * (end - start)
    with open(path, "r+b") as f:
        f.write(buf)
    return len(spans)


def scrub_tree(root):
    log = []
    for top in sorted(os.listdir(root)):
        if top in SCRUB_SKIP or top == "etc":
            continue
        top_path = os.path.join(root, top)
        if os.path.islink(top_path) or not os.path.isdir(top_path):
            continue
        for dp, _, fns in os.walk(top_path):
            for n in fns:
                p = os.path.join(dp, n)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                count = scrub_file(p)
                if count:
                    log.append(f"{os.path.relpath(p, root)}: scrubbed {count} embedded credential(s)")
    return log


def lock_empty_passwords(shadow_text):
    out, locked = [], []
    for line in shadow_text.splitlines(keepends=True):
        fields = line.rstrip("\n").split(":")
        if len(fields) > 1 and fields[1] == "":
            fields[1] = "!"
            locked.append(fields[0])
            line = ":".join(fields) + ("\n" if line.endswith("\n") else "")
        out.append(line)
    return "".join(out), locked


def sanitize(root):
    log = []
    etc = os.path.join(root, "etc")

    conf_path = os.path.join(etc, "pacman.conf")
    conf = _read_text(conf_path)
    if conf is not None:
        new_conf = rewrite_pacman_conf(conf)
        if new_conf != conf:
            _write_text(conf_path, new_conf)
            log.append("pacman.conf: token URLs rewritten to public paths")

    for path in _etc_files(root):
        if path == conf_path:
            continue
        text = _read_text(path)
        if text is None:
            continue
        new = text
        if "/steamos-atomupd/" in path:
            new = strip_credentials(new)
        new = USERINFO_RE.sub(r"\1", new)
        if new != text:
            _write_text(path, new)
            log.append(f"{os.path.relpath(path, root)}: credentials removed")

    shadow = os.path.join(etc, "shadow")
    text = _read_text(shadow)
    if text is not None:
        new, locked = lock_empty_passwords(text)
        if locked:
            _write_text(shadow, new)
            log.append(f"shadow: locked empty passwords for {', '.join(locked)}")

    for path in _etc_files(root):
        text = _read_text(path)
        if text and (TOKEN_RE.search(text) or USERINFO_RE.search(text)):
            raise ValueError(f"{os.path.relpath(path, root)} still contains a secret")

    log += scrub_tree(root)
    return log


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root")
    p.add_argument("--secrets-out", help="write raw secret values, one per line")
    p.add_argument("--secrets-ere-out", help="write ERE-escaped secret values, one per line")
    a = p.parse_args(argv)

    values = collect_secrets(a.root)
    for path, fmt in ((a.secrets_out, str), (a.secrets_ere_out, ere_escape)):
        if path:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.writelines(fmt(v) + "\n" for v in values)
    print(f"collected {len(values)} secret values", file=sys.stderr)

    for line in sanitize(a.root):
        print(line)


if __name__ == "__main__":
    sys.exit(main())
