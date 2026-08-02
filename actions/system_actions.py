"""
actions/system_actions.py
--------------------------
Launches Windows system applications for the accessibility assistant.

Functions
---------
* open_excel()    — launch Microsoft Excel
* open_paint()    — launch Microsoft Paint
* open_notepad()  — launch Notepad

Uses ``subprocess`` with ``shell=True`` and canonical Windows executable
names so the OS resolves paths via PATH / App Paths registry automatically.
Falls back to the ``start`` shell command when the direct executable is not
on PATH (common for Office installations).

Installation
------------
    No extra packages required — uses stdlib only.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger("actions.system")


# ---------------------------------------------------------------------------
# Internal launcher
# ---------------------------------------------------------------------------

def _launch(executable: str, display_name: str, shell_fallback: str | None = None) -> bool:
    """
    Spawn a Windows application by executable name.

    Tries *executable* first.  If that raises ``FileNotFoundError`` and a
    *shell_fallback* is supplied, retries using the fallback command.

    Parameters
    ----------
    executable:
        Executable name or command known to Windows, e.g. ``"notepad.exe"``.
    display_name:
        Human-readable label for log messages.
    shell_fallback:
        Optional secondary command tried when the primary fails,
        e.g. ``"start excel"``.

    Returns
    -------
    bool
        ``True`` if a process was spawned successfully, ``False`` otherwise.
    """
    for cmd in filter(None, [executable, shell_fallback]):
        try:
            logger.info("Launching %s → %r", display_name, cmd)
            subprocess.Popen(
                cmd,
                shell=True,
                # Keep the child alive independently; prevent it from
                # inheriting our console window.
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                ),
            )
            return True
        except FileNotFoundError:
            logger.warning("%r not found, trying fallback …", cmd)
        except OSError as exc:
            logger.error(
                "OS error while launching %s (%r): %s",
                display_name, cmd, exc, exc_info=True,
            )
            return False

    logger.error(
        "%s could not be launched — application may not be installed.", display_name
    )
    return False


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def open_excel() -> bool:
    """
    Launch Microsoft Excel.

    Tries ``excel.exe`` (works when Office is on PATH) then falls back to
    ``start excel`` so Windows can resolve it via file-type associations.

    Returns
    -------
    bool
    """
    return _launch(
        executable="excel.exe",
        display_name="Microsoft Excel",
        shell_fallback="start excel",
    )


def open_paint() -> bool:
    """
    Launch Microsoft Paint (``mspaint.exe``).

    Returns
    -------
    bool
    """
    return _launch(
        executable="mspaint.exe",
        display_name="Microsoft Paint",
    )


def open_notepad() -> bool:
    """
    Launch Notepad (``notepad.exe``).

    Returns
    -------
    bool
    """
    return _launch(
        executable="notepad.exe",
        display_name="Notepad",
    )