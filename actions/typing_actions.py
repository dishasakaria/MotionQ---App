"""
actions/typing_actions.py
--------------------------
Handles keyboard text injection and common key presses.

Functions
---------
* type_text(text)   — type a string into the currently focused window
* press_enter()     — press the Enter / Return key
* backspace()       — press the Backspace key (optionally multiple times)

Uses ``pyautogui`` which calls the Windows SendInput API under the hood,
making it compatible with most native Windows applications including Office.

Installation
------------
    pip install pyautogui
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger("actions.typing")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Delay between individual keystrokes (seconds).
#: 0.04 s (40 ms) mimics natural typing speed and prevents characters
#: being dropped by slower Win32 applications (e.g. older Office builds).
DEFAULT_INTERVAL: float = 0.04

#: Short pause before any typing action so the user has time to click the
#: target window after triggering the voice command.
PRE_TYPE_DELAY: float = 0.3


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
        # Disable the fail-safe corner (top-left) during automated sessions
        # so rapid mouse movements don't abort a typing action.
        pyautogui.FAILSAFE = False
        return pyautogui
    except ImportError as exc:
        raise ImportError(
            "pyautogui is not installed.  Run:  pip install pyautogui"
        ) from exc


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def type_text(text: str, interval: float = DEFAULT_INTERVAL) -> bool:
    """
    Type *text* into the currently focused application window.

    A short pre-type pause is inserted so the user has time to focus the
    target window after issuing the voice command.

    Parameters
    ----------
    text:
        The string to type.  Should already have the trigger keyword stripped
        (e.g. "type hello world" → "hello world" before calling this).
    interval:
        Delay in seconds between each keypress.  Increase to 0.08 for very
        slow applications; decrease to 0.02 for fast modern apps.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if *text* is empty, pyautogui is
        unavailable, or a runtime error occurs.
    """
    if not text or not text.strip():
        logger.warning("type_text() called with empty text — nothing to type.")
        return False

    try:
        pag = _get_pyautogui()
        logger.info(
            "Typing %d character(s) at interval=%.3f s: %r",
            len(text), interval, text,
        )
        time.sleep(PRE_TYPE_DELAY)
        pag.typewrite(text, interval=interval)
        return True
    except ImportError as exc:
        logger.error("%s", exc)
        return False
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to type text: %s", exc, exc_info=True)
        return False


def press_enter() -> bool:
    """
    Press the Enter (Return) key in the active window.

    Useful for confirming dialogs, submitting forms, or ending a line of
    typed text without a follow-up voice command.

    Returns
    -------
    bool
    """
    try:
        pag = _get_pyautogui()
        logger.info("Pressing Enter.")
        pag.press("enter")
        return True
    except ImportError as exc:
        logger.error("%s", exc)
        return False
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to press Enter: %s", exc, exc_info=True)
        return False


def backspace(count: int = 1) -> bool:
    """
    Press the Backspace key one or more times.

    Useful for correcting the last word typed when the ASR makes a mistake.

    Parameters
    ----------
    count:
        Number of times to press Backspace.  Must be ≥ 1.

    Returns
    -------
    bool
    """
    if count < 1:
        logger.warning("backspace() called with count=%d — nothing to do.", count)
        return False

    try:
        pag = _get_pyautogui()
        logger.info("Pressing Backspace × %d.", count)
        for _ in range(count):
            pag.press("backspace")
            # Small delay between presses prevents rapid-fire drops in some apps.
            time.sleep(0.05)
        return True
    except ImportError as exc:
        logger.error("%s", exc)
        return False
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to press Backspace: %s", exc, exc_info=True)
        return False