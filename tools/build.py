#!/usr/bin/env python3
"""Generate the hook commands and build the release archive for auditGuard.

    python tools/build.py                 # commands/ regenerated, dist/auditguard.zip, dist/SHA256SUMS
    python tools/build.py --check         # fail when commands/ is out of date or the versions differ (CI)
    python tools/build.py --check-tag v0.1.0

The 20 hook commands (`speckit.auditguard.<cmd>entry` / `<cmd>exit`) are generated from
templates/hook-command.md, so they never drift apart. The archive has extension.yml at its root, as
`specify extension add --from` expects, and is reproducible: fixed timestamps, sorted entries, normalised modes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)
COMMANDS = ("specify", "clarify", "plan", "tasks", "analyze", "checklist", "constitution", "converge", "implement",
            "taskstoissues")
EXTENSION_FILES = ["extension.yml", "config-template.yml", "README.md", "LICENSE", "CHANGELOG.md"]
EXTENSION_DIRS = ["commands", "scripts", "templates"]


def hook_commands() -> dict:
    template = (ROOT / "templates" / "hook-command.md").read_text(encoding="utf-8")
    out = {}
    for cmd in COMMANDS:
        for kind, event, what in (("entry", f"before_{cmd}", "start"), ("exit", f"after_{cmd}", "end")):
            text = template.replace("{CMD}", cmd).replace("{EVENT}", event).replace("{WHAT}", what)
            out[f"speckit.auditguard.{cmd}{kind}.md"] = text
    return out


def write_commands(check: bool) -> list:
    stale = []
    for name, text in hook_commands().items():
        path = ROOT / "commands" / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(name)
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    return stale


def version_of(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit(f"version not found in {path}")
    return match.group(1)


def versions() -> dict:
    found = {
        "extension.yml": version_of(ROOT / "extension.yml", r'^\s*version:\s*"?([0-9][^"\s]*)"?'),
        "auditguard_core": version_of(ROOT / "scripts" / "python" / "auditguard_core" / "__init__.py",
                                      r'^__version__\s*=\s*"([^"]+)"'),
    }
    data = json.loads((ROOT / "catalog" / "extensions.json").read_text(encoding="utf-8"))
    for entry in (data.get("extensions") or {}).values():
        found["catalog/extensions.json"] = entry["version"]
    return found


def manifest_commands() -> set:
    text = (ROOT / "extension.yml").read_text(encoding="utf-8")
    return set(re.findall(r"file:\s*(commands/[^\s}]+)", text))


def add_file(zf: zipfile.ZipFile, source: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE)
    executable = source.suffix in (".sh", ".py") and "scripts" in source.parts
    info.external_attr = ((0o100755 if executable else 0o100644) & 0xFFFF) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, source.read_bytes())


def collect_extension() -> list:
    entries = [(ROOT / name, name) for name in EXTENSION_FILES]
    for directory in EXTENSION_DIRS:
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                entries.append((path, path.relative_to(ROOT).as_posix()))
    return entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="only check: generated commands in sync, versions equal")
    parser.add_argument("--check-tag", help="fail unless all versions equal this tag (with or without leading v)")
    args = parser.parse_args()

    stale = write_commands(check=args.check)
    if args.check and stale:
        print(f"commands out of date (run python tools/build.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    missing = [c for c in manifest_commands() if not (ROOT / c).is_file()]
    if missing:
        print(f"extension.yml names command files that do not exist: {', '.join(sorted(missing))}", file=sys.stderr)
        return 1
    found = versions()
    if len(set(found.values())) != 1:
        print(f"version mismatch: {found}", file=sys.stderr)
        return 1
    version = next(iter(found.values()))
    if args.check_tag and args.check_tag.lstrip("v") != version:
        print(f"tag {args.check_tag} does not match version {version}", file=sys.stderr)
        return 1
    if args.check:
        print(f"ok: {len(hook_commands())} hook commands in sync, version {version}")
        return 0
    target = DIST / "auditguard.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = collect_extension()
    with zipfile.ZipFile(target, "w") as zf:
        for source, arcname in sorted(entries, key=lambda e: e[1]):
            add_file(zf, source, arcname)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (DIST / "SHA256SUMS").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    print(f"built {target.relative_to(ROOT)} ({len(entries)} files) sha256={digest}")
    print(f"version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
