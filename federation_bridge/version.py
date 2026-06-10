# SPDX-License-Identifier: AGPL-3.0-or-later
"""Application version, shown in the startup log, the Web UI and /health.

The version is the semantic release number plus, when the app runs from a git
checkout, the current short commit id (e.g. ``1.1.0+3035b8b``). Installs
without a .git directory (e.g. the setup.sh/systemd layout) fall back to the
plain release number.
"""

import os
import subprocess

__version__ = "1.1.0"

_resolved: str | None = None


def _git_commit() -> str:
    """Short commit id of the running checkout, or '' if not determinable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def get_version() -> str:
    """Full version string, resolved once and cached."""
    global _resolved
    if _resolved is None:
        commit = _git_commit()
        _resolved = f"{__version__}+{commit}" if commit else __version__
    return _resolved
