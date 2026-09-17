"""Launcher package for desktop GUI application and single-instance management."""

from quantdesk.launcher.app import LauncherApp
from quantdesk.launcher.errors import DiagnosticIssue, diagnose_environment

__all__ = [
    "DiagnosticIssue",
    "LauncherApp",
    "diagnose_environment",
]
