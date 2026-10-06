#!/usr/bin/env python3
"""auditGuard - the audit trail of the GitHub Spec Kit SDLC (launcher for the engine in auditguard_core/).

    python .specify/extensions/auditguard/scripts/python/auditguard.py <command> [options]

Standard library only, Python 3.9+. Exit codes: 0 ok, 1 verify/check found problems, 2 cannot run,
3 a command reserved for people ran in an agent context.
"""

import sys
from pathlib import Path

if sys.version_info < (3, 9):
    sys.stderr.write("auditGuard: ERROR: Python 3.9 or newer is required\n")
    sys.exit(2)

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from auditguard_core.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
