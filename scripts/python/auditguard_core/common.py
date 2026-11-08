"""Shared helpers: paths, hashing, time, git, globbing, feature resolution and Markdown tables.

Several helpers follow archiGuard's `archiguard_core.common` so the Guardians family hashes, globs and
resolves features the same way (for example the line-ending-normalised file hash that archiGuard's
sign-off records, which the golden checks compare against).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

EXIT_OK = 0         # recorded / verified / pass
EXIT_PROBLEMS = 1   # verify or check found problems
EXIT_ERROR = 2      # cannot run: bad config, broken chain on append, missing repository, ...
EXIT_BLOCKED = 3    # a command reserved for people was run from an agent context

SPECIFY_DIR = Path(".specify")
EXTENSION_ID = "auditguard"
EXTENSION_REL = SPECIFY_DIR / "extensions" / EXTENSION_ID
CONFIG_REL = EXTENSION_REL / "auditguard-config.yml"
LOCAL_CONFIG_RELS = (EXTENSION_REL / "local-config.yml", EXTENSION_REL / "auditguard-config.local.yml")
EXTENSIONS_YML = SPECIFY_DIR / "extensions.yml"
WORK_REL = SPECIFY_DIR / "auditguard"           # workstation state (git-ignored)
STATE_REL = WORK_REL / "state"
PROJECT_KEY = "_project"
UNASSIGNED = "_unassigned"
GENESIS = "0" * 64

# The ten Spec Kit commands that surface before_/after_ hooks.
SPECKIT_COMMANDS = ("specify", "clarify", "plan", "tasks", "analyze", "checklist", "constitution",
                    "converge", "implement", "taskstoissues")
HOOK_EVENTS = tuple(f"{when}_{cmd}" for cmd in SPECKIT_COMMANDS for when in ("before", "after"))
RUNTIME_EVENTS = ("session_start", "stop", "session_end")


class AuditGuardError(Exception):
    """A problem the user has to fix (configuration, missing input, broken chain)."""

    def __init__(self, message: str, code: int = EXIT_ERROR):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# Environment and time                                                          #
# --------------------------------------------------------------------------- #


def env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_ci() -> bool:
    return env_true("CI") or env_true("GITHUB_ACTIONS") or env_true("AUDITGUARD_CI")


def now() -> _dt.datetime:
    fixed = os.environ.get("AUDITGUARD_NOW", "").strip()   # tests and reproducible examples
    if fixed:
        parsed = parse_time(fixed)
        if parsed is not None:
            return parsed
    return _dt.datetime.now().astimezone().replace(microsecond=0)


def now_iso() -> str:
    return now().isoformat()


def today() -> _dt.date:
    return now().date()


def parse_time(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = _dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def parse_date(value: Any) -> Optional[_dt.date]:
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def time_key(value: Any) -> float:
    parsed = parse_time(value)
    return parsed.timestamp() if parsed else 0.0


# --------------------------------------------------------------------------- #
# Files and hashing                                                             #
# --------------------------------------------------------------------------- #


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AuditGuardError(f"cannot read {path}: {exc}")


def read_text(path: Path) -> str:
    data = read_bytes(path)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def line_ending(path: Path) -> str:
    """The line ending most lines of an existing file use (CRLF or LF; LF on a tie). A rewrite of a file another tool
    owns (Spec Kit writes .specify/extensions.yml with the platform's ending) keeps it, so git shows only the edited
    lines. Never used for the audit trail, which is LF byte for byte."""
    try:
        data = path.read_bytes()
        return "\r\n" if 2 * data.count(b"\r\n") > data.count(b"\n") else "\n"
    except OSError:
        return "\n"


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(read_text(path))
    except ValueError:
        return default


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def normalise_content(data: bytes) -> bytes:
    """Text content with LF line endings and no BOM; binary content (a NUL byte) unchanged.

    Every artefact, evidence and blob hash in auditGuard goes through this, so a Windows checkout
    (CRLF) and the blob in git (LF) hash the same - and the hashes agree with archiGuard's sign-off.
    """
    if b"\x00" in data[:8192]:
        return data
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def content_hash(data: bytes) -> str:
    return sha256_bytes(normalise_content(data))


def file_hash(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return content_hash(path.read_bytes())
    except OSError:
        return None


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_json(data: Any) -> str:
    return sha256_text(canonical_json(data))


def rel_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def short(h: Optional[str], n: int = 12) -> str:
    if not h:
        return "-"
    return h.split(":", 1)[-1][:n]


# --------------------------------------------------------------------------- #
# Globbing ("**" aware, brace expansion) - same semantics as archiGuard         #
# --------------------------------------------------------------------------- #

_GLOB_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _expand_braces(pattern: str) -> List[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    out: List[str] = []
    for option in match.group(1).split(","):
        out.extend(_expand_braces(pattern[: match.start()] + option + pattern[match.end():]))
    return out


def _glob_to_regex(pattern: str) -> str:
    i, n, out = 0, len(pattern), []
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 2] == "**":
                if pattern[i:i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                else:
                    out.append(".*")
                    i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def compile_glob(pattern: str) -> "re.Pattern[str]":
    cached = _GLOB_CACHE.get(pattern)
    if cached is not None:
        return cached
    pat = pattern.strip().replace("\\", "/")
    while pat.startswith("./"):
        pat = pat[2:]
    compiled = re.compile("^(?:" + "|".join(_glob_to_regex(p) for p in _expand_braces(pat)) + ")$")
    _GLOB_CACHE[pattern] = compiled
    return compiled


def glob_match(path: str, patterns: Iterable[str]) -> bool:
    p = path.replace("\\", "/")
    return any(compile_glob(g).match(p) for g in patterns)


SCAN_EXCLUDES = (".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build", "dist", ".idea", ".vscode")


def walk_files(base: Path, excludes: Sequence[str] = SCAN_EXCLUDES) -> Iterator[str]:
    names = set(excludes)
    base = base.resolve()
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in names)
        rel_dir = Path(current).relative_to(base).as_posix()
        for name in sorted(files):
            yield name if rel_dir == "." else f"{rel_dir}/{name}"


# --------------------------------------------------------------------------- #
# git                                                                           #
# --------------------------------------------------------------------------- #


def git(root: Path, *args: str, timeout: int = 60, input_bytes: Optional[bytes] = None) -> Tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, timeout=timeout,
            input=input_bytes if input_bytes is not None else None,
            stdin=None if input_bytes is not None else subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    out = proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout)
    return proc.returncode, out.decode("utf-8", errors="replace")


def git_bytes(root: Path, *args: str, timeout: int = 60) -> Tuple[int, bytes]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc).encode()
    return proc.returncode, proc.stdout if proc.returncode == 0 else proc.stderr


def is_git_repo(root: Path) -> bool:
    """True when the project root is the root of a git work tree (paths in the trail are git paths then).

    A Spec Kit project in a subfolder of a larger repository is recorded without git fields; the golden
    checks report it as skipped."""
    code, out = git(root, "rev-parse", "--show-toplevel")
    if code != 0 or not out.strip():
        return False
    try:
        return Path(out.strip()).resolve() == root.resolve()
    except OSError:
        return False


def git_toplevel(root: Path) -> Optional[str]:
    code, out = git(root, "rev-parse", "--show-toplevel")
    return out.strip() if code == 0 and out.strip() else None


def git_head(root: Path) -> Optional[str]:
    code, out = git(root, "rev-parse", "HEAD")
    return out.strip() if code == 0 else None


def git_branch(root: Path) -> str:
    code, out = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if code == 0 else ""


def git_user(root: Path) -> Optional[str]:
    code, out = git(root, "config", "user.name")
    return out.strip() or None if code == 0 else None


# --------------------------------------------------------------------------- #
# Project and feature resolution (the way Spec Kit finds the active feature)    #
# --------------------------------------------------------------------------- #


def find_project_root(explicit: Optional[str] = None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise AuditGuardError(f"--root {explicit} is not a directory")
        return root
    here = Path.cwd().resolve()
    for candidate in (here, *here.parents):
        if (candidate / SPECIFY_DIR).is_dir():
            return candidate
    return here


def feature_dirs(root: Path) -> List[Path]:
    specs = root / "specs"
    if not specs.is_dir():
        return []
    return sorted(p for p in specs.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))


def resolve_feature_dir(root: Path, explicit: Optional[str] = None, *, required: bool = True) -> Optional[Path]:
    """The active feature: --feature-dir, SPECIFY_FEATURE_DIRECTORY, .specify/feature.json, branch, the only one."""
    def _check(path: Path, source: str) -> Optional[Path]:
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_dir():
            if required:
                raise AuditGuardError(f"feature directory from {source} does not exist: {path}")
            return None
        return path

    if explicit:
        p = Path(explicit)
        if not p.is_absolute() and not (root / p).exists() and (Path.cwd() / p).exists():
            p = Path.cwd() / p
        return _check(p, "--feature-dir")
    env = os.environ.get("SPECIFY_FEATURE_DIRECTORY", "").strip()
    if env:
        return _check(Path(env), "SPECIFY_FEATURE_DIRECTORY")
    pointer = root / SPECIFY_DIR / "feature.json"
    if pointer.is_file():
        data = read_json(pointer, {})
        value = data.get("feature_directory") if isinstance(data, dict) else None
        if value:
            found = _check(Path(value), ".specify/feature.json")
            if found is not None:
                return found
    dirs = feature_dirs(root)
    branch = git_branch(root)
    if branch:
        for d in dirs:
            if d.name == branch or branch.endswith("/" + d.name):
                return d.resolve()
        num = re.match(r"^(\d{3,})-", branch.split("/")[-1])
        if num:
            matches = [d for d in dirs if d.name.startswith(num.group(1) + "-")]
            if len(matches) == 1:
                return matches[0].resolve()
    if len(dirs) == 1:
        return dirs[0].resolve()
    if not required:
        return None
    if not dirs:
        raise AuditGuardError("no feature directory found under specs/ (pass --feature-dir)")
    raise AuditGuardError("cannot tell which feature is active - pass --feature-dir specs/<feature> "
                          "(or set SPECIFY_FEATURE_DIRECTORY)")


def feature_key(feature_dir: Optional[Path]) -> str:
    return feature_dir.name if feature_dir is not None else PROJECT_KEY


# --------------------------------------------------------------------------- #
# Markdown sections and tables                                                  #
# --------------------------------------------------------------------------- #

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def section_lines(text: str, title_regex: str) -> Optional[Tuple[int, List[str]]]:
    """(first line number, lines) of the first section whose heading matches; None when absent."""
    lines = strip_html_comments(text).split("\n")
    rx = re.compile(title_regex, re.I)
    in_fence = False
    start: Optional[int] = None
    level = 0
    for idx, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        if start is not None and len(m.group(1)) <= level:
            return start + 1, lines[start:idx]
        if start is None and rx.search(m.group(2)):
            start, level = idx + 1, len(m.group(1))
    if start is not None:
        return start + 1, lines[start:]
    return None


def split_row(line: str) -> List[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells, cur, i = [], [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def first_table(lines: List[str], first_line: int) -> Tuple[List[str], List[Tuple[int, List[str]]]]:
    """(header, [(line number, cells)]) of the first pipe table in the lines."""
    for i in range(len(lines) - 1):
        if "|" in lines[i] and _SEPARATOR_RE.match(lines[i + 1] or ""):
            header = [clean_cell(c).lower() for c in split_row(lines[i])]
            rows = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append((first_line + j, split_row(lines[j])))
                j += 1
            return header, rows
    return [], []


def clean_cell(cell: str) -> str:
    text = re.sub(r"[*_`]", "", cell or "").strip()
    return re.sub(r"\s+", " ", text)


def column(header: List[str], *keywords: str) -> Optional[int]:
    for kw in keywords:
        for idx, name in enumerate(header):
            if kw in name:
                return idx
    return None


def cell(cells: List[str], idx: Optional[int]) -> str:
    if idx is None or idx >= len(cells):
        return ""
    return clean_cell(cells[idx])


def version_tuple(text: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", str(text).split("+")[0])
    return tuple(int(p) for p in parts[:3]) or (0,)


def version_satisfies(version: str, constraint: str) -> bool:
    if not constraint:
        return True
    v = version_tuple(version)
    for clause in str(constraint).split(","):
        clause = clause.strip()
        m = re.match(r"^(>=|<=|==|!=|>|<|~=)?\s*([0-9][0-9A-Za-z.\-+]*)$", clause)
        if not m:
            continue
        op, target = m.group(1) or "==", version_tuple(m.group(2))
        width = max(len(v), len(target))
        a = v + (0,) * (width - len(v))
        b = target + (0,) * (width - len(target))
        ok = {">=": a >= b, "<=": a <= b, "==": a == b, "!=": a != b, ">": a > b, "<": a < b,
              "~=": a >= b and a[: max(1, len(target) - 1)] == b[: max(1, len(target) - 1)]}[op]
        if not ok:
            return False
    return True
