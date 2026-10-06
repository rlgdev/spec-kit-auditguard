"""Packaging: the manifest, the generated hook commands, the launchers, the catalog, the archive."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile

import pytest

from auditguard_core import __version__, yamlio
from auditguard_core.common import HOOK_EVENTS

from conftest import REPO


def manifest():
    return yamlio.load_file(REPO / "extension.yml")


def test_manifest_covers_every_hook_and_event():
    m = manifest()
    assert set(m["hooks"]) == set(HOOK_EVENTS)
    assert all(h["optional"] is False for h in m["hooks"].values())
    assert set(m["events"]) == {"session_start", "stop", "session_end", "pre_tool_use"}
    names = {c["name"] for c in m["provides"]["commands"]}
    for spec in list(m["hooks"].values()) + list(m["events"].values()):
        assert spec["command"] in names
    for c in m["provides"]["commands"]:
        assert (REPO / c["file"]).is_file(), c["file"]
    assert m["extension"]["version"] == __version__


def test_command_scripts_point_at_real_files():
    for path in (REPO / "commands").glob("*.md"):
        text = path.read_text(encoding="utf-8")
        front = text.split("---")[1]
        for line in front.splitlines():
            m = re.match(r"^\s+(sh|ps|py):\s*(?:bash\s+)?(\S+)", line)
            if m:
                assert (REPO / m.group(2)).is_file(), f"{path.name}: {m.group(2)}"


def test_generated_commands_are_in_sync():
    proc = subprocess.run([sys.executable, str(REPO / "tools/build.py"), "--check"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_catalog_matches():
    data = json.loads((REPO / "catalog/extensions.json").read_text(encoding="utf-8"))
    entry = data["extensions"]["auditguard"]
    m = manifest()
    assert entry["version"] == __version__
    assert entry["provides"] == {"commands": len(m["provides"]["commands"]), "hooks": len(m["hooks"]),
                                 "events": len(m["events"])}


def test_archive_layout(tmp_path):
    proc = subprocess.run([sys.executable, str(REPO / "tools/build.py")], capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, proc.stderr
    names = zipfile.ZipFile(REPO / "dist/auditguard.zip").namelist()
    assert "extension.yml" in names and "scripts/python/auditguard.py" in names
    assert "templates/viewer/index.html" in names
    assert not any("__pycache__" in n or n.startswith(("tests/", "examples/", "docs/")) for n in names)


def test_python_launcher_version():
    proc = subprocess.run([sys.executable, str(REPO / "scripts/python/auditguard.py"), "version"], capture_output=True, text=True)
    assert proc.stdout.strip() == f"auditGuard {__version__}"


@pytest.mark.skipif(sys.platform == "win32", reason="bash from a Windows subprocess is the WSL launcher; "
                    "the CI launchers job runs the bash launcher in Git Bash")
def test_bash_launcher(tmp_path):
    import shutil
    if not shutil.which("bash"):
        pytest.skip("no bash")
    proc = subprocess.run(["bash", str(REPO / "scripts/bash/auditguard.sh"), "version"], capture_output=True, text=True)
    assert proc.returncode == 0 and __version__ in proc.stdout
