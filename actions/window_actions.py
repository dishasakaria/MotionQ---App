"""
actions/window_actions.py
--------------------------
Simulates keyboard shortcuts for window and presentation control.

Functions
---------
* next_slide()      — advance to the next slide (Right Arrow)
* previous_slide()  — go back to the previous slide (Left Arrow)
* alt_tab()         — switch to the previous application (Alt+Tab)
* close_window()    — close the active window (Alt+F4)

Uses ``pyautogui`` which calls the Windows SendInput API, making shortcuts
compatible with most native Win32 and Office applications.

Installation
------------
    pip install pyautogui
"""

from __future__ import annotations

import logging

logger = logging.getLogger("actions.window")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _get_pyautogui():
    """
    Lazy-import pyautogui and return the module.

    Raises a clear ``ImportError`` with install instructions if missing.
    """
    try:
        import pyautogui
        return pyautogui
    except ImportError as exc:
        raise ImportError(
            "pyautogui is not installed.  Run:  pip install pyautogui"
        ) from exc


def _hotkey(display_label: str, *keys: str) -> bool:
    """
    Press a keyboard hotkey combination via pyautogui.

    Parameters
    ----------
    display_label:
        Human-readable description for logging.
    *keys:
        Key names passed to ``pyautogui.hotkey()``, e.g. ``"alt", "tab"``.

    Returns
    -------
    bool
    """
    try:
        pag = _get_pyautogui()
        logger.info("Hotkey: %s → %s", display_label, " + ".join(keys))
        pag.hotkey(*keys)
        return True
    except ImportError as exc:
        logger.error("%s", exc)
        return False
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to send hotkey [%s]: %s", display_label, exc, exc_info=True)
        return False


def _press(display_label: str, key: str) -> bool:
    """
    Press a single key via pyautogui.

    Parameters
    ----------
    display_label:
        Human-readable description for logging.
    key:
        Key name passed to ``pyautogui.press()``.

    Returns
    -------
    bool
    """
    try:
        pag = _get_pyautogui()
        logger.info("Key press: %s → %r", display_label, key)
        pag.press(key)
        return True
    except ImportError as exc:
        logger.error("%s", exc)
        return False
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to press key [%s]: %s", display_label, exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def next_slide() -> bool:
    """
    Advance to the next slide in the active presentation window.

    Sends the Right Arrow key — the universal next-slide shortcut in
    PowerPoint, LibreOffice Impress, and most full-screen slide viewers.

    Returns
    -------
    bool
    """
    return _press("Next Slide", "right")


def previous_slide() -> bool:
    """
    Go back to the previous slide in the active presentation window.

    Sends the Left Arrow key.

    Returns
    -------
    bool
    """
    return _press("Previous Slide", "left")


def alt_tab() -> bool:
    """
    Switch to the most recently used application (Alt + Tab).

    Returns
    -------
    bool
    """
    return _hotkey("Alt+Tab", "alt", "tab")


def close_window() -> bool:
    """
    Close the currently active window (Alt + F4).

    Returns
    -------
    bool
    """
    return _hotkey("Close Window (Alt+F4)", "alt", "f4")