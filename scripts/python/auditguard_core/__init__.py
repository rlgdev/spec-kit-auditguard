"""auditGuard engine - a tamper-evident audit trail of the Spec Kit SDLC: every command by stage, the gate
reports of scopeGuard and archiGuard, the waiver history, the human decisions and the changes outside
recorded commands, per sprint and feature, verifiable against git and the other golden sources.

Standard library only; Python 3.9+. PyYAML is used when importable.
"""

__version__ = "0.2.0"
TOOL = "auditguard"
