"""
actions/browser_actions.py
--------------------------
Handles all browser / web-related actions for the accessibility assistant.

Functions
---------
* open_google()          — open google.com in the default browser
* open_youtube()         — open youtube.com in the default browser
* search_google(query)   — open a Google search results page for a query

Windows-compatible.  Uses the stdlib ``webbrowser`` module so it honours
the user's default browser with no extra dependencies.

Installation
------------
    No extra packages required — uses stdlib only.
"""

from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote_plus

logger = logging.getLogger("actions.browser")


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _open_url(url: str, label: str) -> bool:
    """
    Open *url* in the system default browser.

    Parameters
    ----------
    url:
        Fully-qualified URL to open.
    label:
        Human-readable description used in log messages.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the browser could not be launched.
    """
    try:
        logger.info("Opening browser → [%s] %s", label, url)
        webbrowser.open(url)
        return True
    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to open %s: %s", label, exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def open_google() -> bool:
    """
    Open https://www.google.com in the system default browser.

    Returns
    -------
    bool
    """
    return _open_url("https://www.google.com", "Google")


def open_youtube() -> bool:
    """
    Open https://www.youtube.com in the system default browser.

    Returns
    -------
    bool
    """
    return _open_url("https://www.youtube.com", "YouTube")


def search_google(query: str) -> bool:
    """
    Open a Google search results page for *query*.

    The query is URL-encoded so arbitrary text (including Indian-script
    characters and punctuation from ASR output) is passed safely.

    Parameters
    ----------
    query:
        Raw search string, e.g. ``"weather in Mumbai"`` or ``"open source AI"``.

    Returns
    -------
    bool
        ``False`` if *query* is empty or the browser could not be launched.
    """
    if not query or not query.strip():
        logger.warning("search_google() called with empty query — nothing to do.")
        return False

    encoded = quote_plus(query.strip())
    url = f"https://www.google.com/search?q={encoded}"
    return _open_url(url, f"Google search: {query!r}")