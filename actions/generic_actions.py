"""
actions/generic_actions.py
--------------------------
Dynamic action system for the offline Windows accessibility assistant.

Unlike the static action modules (browser_actions, system_actions, etc.) which
hard-code a fixed set of targets, this module accepts *arbitrary* runtime
parameters — the application name, website, query, or text to type are all
determined at call time from the user's speech.

Functions
---------
* open_application(app_name)  — launch any Windows application by name
* open_website(site_name)     — open any website in the default browser
* search_google(query)        — perform a Google search with any query string
* type_text(text)             — type any text into the focused window

Design goals
------------
* Fuzzy app resolution   — "vs code" → "code.exe", "word" → "winword.exe"
* Website normalisation  — "youtube" → "https://youtube.com"
* URL-safe search        — arbitrary query strings are percent-encoded
* Graceful degradation   — every failure path logs a clear error and returns False
* No ML / no internet    — fully offline; only stdlib + optional pyautogui

Installation
------------
    pip install pyautogui   # required only for type_text()
    # webbrowser and subprocess are stdlib — no install needed

Usage
-----
    from actions.generic_actions import (
        open_application, open_website, search_google, type_text
    )

    open_application("excel")
    open_application("vscode")
    open_website("youtube")
    open_website("github.com")
    search_google("best accessibility software")
    type_text("hello, my name is Disha")
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
import webbrowser
from urllib.parse import quote_plus, urlparse

logger = logging.getLogger("actions.generic")


# ---------------------------------------------------------------------------
# Application name → executable mapping
# ---------------------------------------------------------------------------

# Maps common spoken / typed aliases to their Windows executable names.
# Keys are lowercase; values are passed to subprocess / Windows shell.
#
# To add a new app: add an entry here — no other code needs to change.
_APP_ALIASES: dict[str, str] = {
    # Microsoft Office
    "excel":          "excel.exe",
    "word":           "winword.exe",
    "powerpoint":     "powerpnt.exe",
    "ppt":            "powerpnt.exe",
    "outlook":        "outlook.exe",
    "onenote":        "onenote.exe",
    "access":         "msaccess.exe",
    "teams":          "teams.exe",

    # Windows built-ins
    "notepad":        "notepad.exe",
    "paint":          "mspaint.exe",
    "calculator":     "calc.exe",
    "calc":           "calc.exe",
    "explorer":       "explorer.exe",
    "file explorer":  "explorer.exe",
    "task manager":   "taskmgr.exe",
    "control panel":  "control.exe",
    "settings":       "ms-settings:",       # UWP URI
    "camera":         "microsoft.windows.camera:",
    "calendar":       "outlookcal:",
    "mail":           "outlookmail:",
    "snipping tool":  "snippingtool.exe",
    "snip":           "snippingtool.exe",
    "wordpad":        "wordpad.exe",
    "cmd":            "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell":     "powershell.exe",
    "terminal":       "wt.exe",             # Windows Terminal

    # Browsers
    "chrome":         "chrome.exe",
    "google chrome":  "chrome.exe",
    "firefox":        "firefox.exe",
    "edge":           "msedge.exe",
    "microsoft edge": "msedge.exe",
    "opera":          "opera.exe",
    "brave":          "brave.exe",

    # Developer tools
    "vscode":         "code.exe",
    "vs code":        "code.exe",
    "visual studio code": "code.exe",
    "visual studio":  "devenv.exe",
    "git bash":       "git-bash.exe",
    "pycharm":        "pycharm64.exe",
    "intellij":       "idea64.exe",
    "android studio": "studio64.exe",
    "postman":        "postman.exe",
    "github desktop": "githubdesktop.exe",

    # Media / utilities
    "vlc":            "vlc.exe",
    "spotify":        "spotify.exe",
    "zoom":           "zoom.exe",
    "discord":        "discord.exe",
    "whatsapp":       "whatsapp.exe",
    "telegram":       "telegram.exe",
    "slack":          "slack.exe",
    "obs":            "obs64.exe",
    "7zip":           "7zfm.exe",
    "winrar":         "winrar.exe",
    "acrobat":        "acrobat.exe",
}

# Suffixes that ASR commonly appends but that are not part of the app name.
_APP_NOISE_PATTERN = re.compile(
    r"\s+(?:application|app|program|software|tool|window|file)$",
    re.IGNORECASE,
)


def _resolve_app(app_name: str) -> str:
    """
    Resolve a spoken application name to a Windows executable string.

    Resolution order
    ----------------
    1. Exact match in ``_APP_ALIASES`` (after lowercasing + noise removal).
    2. Partial / substring match in ``_APP_ALIASES``.
    3. Passthrough — append ``.exe`` if no extension is present and let
       the Windows shell resolve it via PATH / App Paths registry.

    Parameters
    ----------
    app_name:
        Raw application name from speech, e.g. ``"vs code"`` or ``"vlc"``.

    Returns
    -------
    str
        Executable string ready to pass to ``subprocess.Popen``.
    """
    cleaned = _APP_NOISE_PATTERN.sub("", app_name.lower().strip())

    # 1 — exact alias lookup
    if cleaned in _APP_ALIASES:
        return _APP_ALIASES[cleaned]

    # 2 — partial match (longest key that is a substring of cleaned wins)
    candidates = [
        (key, exe)
        for key, exe in _APP_ALIASES.items()
        if key in cleaned or cleaned in key
    ]
    if candidates:
        # prefer longer (more specific) key
        best_key, best_exe = max(candidates, key=lambda kv: len(kv[0]))
        logger.debug("Partial alias match: %r → %r", cleaned, best_exe)
        return best_exe

    # 3 — passthrough: add .exe if caller didn't supply an extension
    if "." not in cleaned and ":" not in cleaned:
        passthrough = cleaned.replace(" ", "") + ".exe"
    else:
        passthrough = cleaned

    logger.debug("No alias for %r — using passthrough: %r", cleaned, passthrough)
    return passthrough


# ---------------------------------------------------------------------------
# Website normalisation
# ---------------------------------------------------------------------------

# Known spoken names → canonical domain (no scheme).
_SITE_ALIASES: dict[str, str] = {
    "youtube":    "youtube.com",
    "google":     "google.com",
    "gmail":      "gmail.com",
    "amazon":     "amazon.in",
    "flipkart":   "flipkart.com",
    "github":     "github.com",
    "wikipedia":  "wikipedia.org",
    "twitter":    "twitter.com",
    "instagram":  "instagram.com",
    "facebook":   "facebook.com",
    "linkedin":   "linkedin.com",
    "whatsapp":   "web.whatsapp.com",
    "netflix":    "netflix.com",
    "spotify":    "open.spotify.com",
    "maps":       "maps.google.com",
    "translate":  "translate.google.com",
    "drive":      "drive.google.com",
    "chatgpt":    "chat.openai.com",
    "openai":     "openai.com",
    "stackoverflow": "stackoverflow.com",
    "reddit":     "reddit.com",
    "medium":     "medium.com",
    "notion":     "notion.so",
    "figma":      "figma.com",
    "canva":      "canva.com",
}

# Matches a bare domain spoken or typed by the user (e.g. "github.com").
_DOMAIN_RE = re.compile(
    r"^[\w-]+\.(?:com|in|org|net|io|co|edu|gov|ai|dev|app|tech)\b",
    re.IGNORECASE,
)


def _resolve_url(site_name: str) -> str:
    """
    Normalise a spoken site name to a full HTTPS URL.

    Resolution order
    ----------------
    1. Already a full URL (has a scheme) → return as-is.
    2. Exact alias lookup.
    3. Partial alias match.
    4. Bare domain pattern → prepend ``https://``.
    5. Passthrough → assume ``.com`` and prepend ``https://``.

    Parameters
    ----------
    site_name:
        Spoken site reference, e.g. ``"youtube"``, ``"github.com"``,
        or ``"https://example.com"``.

    Returns
    -------
    str
        A fully-qualified URL starting with ``https://``.
    """
    cleaned = site_name.lower().strip()
    # Remove qualifiers like "website", "site", "page"
    cleaned = re.sub(r"\s+(?:website|site|page|dot com)$", "", cleaned).strip()

    # 1 — already a full URL
    parsed = urlparse(cleaned)
    if parsed.scheme in ("http", "https", "ftp"):
        return cleaned

    # 2 — exact alias
    if cleaned in _SITE_ALIASES:
        return f"https://{_SITE_ALIASES[cleaned]}"

    # 3 — partial alias match
    candidates = [
        domain for key, domain in _SITE_ALIASES.items()
        if key in cleaned or cleaned in key
    ]
    if candidates:
        return f"https://{candidates[0]}"

    # 4 — bare domain (e.g. "github.com")
    if _DOMAIN_RE.match(cleaned):
        return f"https://{cleaned}"

    # 5 — unknown name: guess <name>.com
    slug = re.sub(r"\s+", "", cleaned)   # "stack overflow" → "stackoverflow"
    logger.debug("Unknown site %r — guessing https://%s.com", site_name, slug)
    return f"https://{slug}.com"


# ---------------------------------------------------------------------------
# open_application
# ---------------------------------------------------------------------------

def open_application(app_name: str) -> bool:
    """
    Launch an arbitrary Windows application by spoken name.

    The name is resolved through an alias table then passed to the Windows
    shell so that App Paths registry entries are also consulted.

    Parameters
    ----------
    app_name:
        Spoken application name, e.g. ``"excel"``, ``"vscode"``,
        ``"vlc"``, ``"chrome"``.

    Returns
    -------
    bool
        ``True`` if a process was successfully spawned, ``False`` otherwise.

    Examples
    --------
    >>> open_application("excel")      # resolves to excel.exe
    >>> open_application("vscode")     # resolves to code.exe
    >>> open_application("calculator") # resolves to calc.exe
    """
    if not app_name or not app_name.strip():
        logger.warning("open_application() called with empty app_name.")
        return False

    executable = _resolve_app(app_name)
    logger.info("open_application(%r) → launching %r", app_name, executable)

    try:
        subprocess.Popen(
            executable,
            shell=True,
            # Detach from our process so the launched app outlives us.
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.DETACHED_PROCESS
            ),
        )
        logger.info("✓ Launched: %s", executable)
        return True

    except FileNotFoundError:
        logger.error(
            "Application not found: %r.  "
            "Ensure it is installed and either on PATH or in App Paths registry.",
            executable,
        )
        return False

    except PermissionError:
        logger.error(
            "Permission denied launching %r.  "
            "Try running the assistant with elevated privileges.",
            executable,
        )
        return False

    except OSError as exc:
        logger.error("OS error launching %r: %s", executable, exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# open_website
# ---------------------------------------------------------------------------

def open_website(site_name: str) -> bool:
    """
    Open an arbitrary website in the system default browser.

    The site name is resolved through an alias table and normalised to a
    full HTTPS URL before being passed to ``webbrowser.open()``.

    Parameters
    ----------
    site_name:
        Spoken site name or domain, e.g. ``"youtube"``, ``"github.com"``,
        ``"amazon"``, ``"https://example.com"``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if the browser could not be launched.

    Examples
    --------
    >>> open_website("youtube")       # opens https://youtube.com
    >>> open_website("github.com")    # opens https://github.com
    >>> open_website("my blog")       # guesses https://myblog.com
    """
    if not site_name or not site_name.strip():
        logger.warning("open_website() called with empty site_name.")
        return False

    url = _resolve_url(site_name)
    logger.info("open_website(%r) → opening %r", site_name, url)

    try:
        webbrowser.open(url)
        logger.info("✓ Opened website: %s", url)
        return True

    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to open website %r: %s", url, exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# search_google
# ---------------------------------------------------------------------------

def search_google(query: str) -> bool:
    """
    Open a Google search results page for an arbitrary query.

    The query is URL-encoded via ``urllib.parse.quote_plus`` so that
    punctuation, spaces, and non-ASCII characters (e.g. Devanagari from
    ASR output) are transmitted correctly.

    Parameters
    ----------
    query:
        Raw search string extracted from speech, e.g.
        ``"best accessibility software"`` or ``"Python asyncio tutorial"``.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if *query* is empty or the browser
        could not be launched.

    Examples
    --------
    >>> search_google("best accessibility software")
    >>> search_google("weather in Mumbai today")
    >>> search_google("how to say hello in Tamil")
    """
    if not query or not query.strip():
        logger.warning("search_google() called with empty query.")
        return False

    encoded = quote_plus(query.strip())
    url = f"https://www.google.com/search?q={encoded}"
    logger.info("search_google(%r) → %s", query, url)

    try:
        webbrowser.open(url)
        logger.info("✓ Google search opened: %r", query)
        return True

    except Exception as exc:           # noqa: BLE001
        logger.error(
            "Failed to open Google search for %r: %s", query, exc, exc_info=True
        )
        return False


# ---------------------------------------------------------------------------
# type_text
# ---------------------------------------------------------------------------

#: Delay between individual keystrokes (seconds).
#: 40 ms balances typing speed with reliability across Win32 apps.
_TYPE_INTERVAL: float = 0.04

#: Short pause before typing starts so the user can focus the target window.
_PRE_TYPE_PAUSE: float = 0.3


def type_text(text: str, interval: float = _TYPE_INTERVAL) -> bool:
    """
    Type an arbitrary string into the currently focused application window.

    Uses ``pyautogui.typewrite()`` which calls the Windows SendInput API,
    making it compatible with most native desktop and Office applications.

    A short pre-type pause is added so the user has time to click the
    intended target window after issuing the voice command.

    Parameters
    ----------
    text:
        The string to type.  Should already have trigger keywords stripped
        (e.g. ``"type hello world"`` → ``"hello world"`` before calling this).
    interval:
        Delay in seconds between each keypress.  Increase to ``0.08`` for
        older/slower applications; decrease to ``0.02`` for fast modern ones.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if *text* is empty, pyautogui is
        unavailable, or a runtime error occurs.

    Examples
    --------
    >>> type_text("hello, my name is Disha")
    >>> type_text("import numpy as np", interval=0.02)
    """
    if not text or not text.strip():
        logger.warning("type_text() called with empty text.")
        return False

    try:
        import pyautogui          # lazy import — optional dependency
        pyautogui.FAILSAFE = False
    except ImportError:
        logger.error(
            "pyautogui is not installed.  Run:  pip install pyautogui"
        )
        return False

    try:
        logger.info(
            "type_text() — %d char(s) at %.3f s/key: %r",
            len(text), interval, text,
        )
        # Give the user a moment to focus the destination window.
        time.sleep(_PRE_TYPE_PAUSE)
        pyautogui.typewrite(text, interval=interval)
        logger.info("✓ Text typed successfully.")
        return True

    except Exception as exc:           # noqa: BLE001
        logger.error("Failed to type text: %s", exc, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Example / smoke-test  (run file directly)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Resolution smoke-tests (no side-effects — just verifies name → exe/url).
    print("\n── App resolution ──────────────────────────────────────")
    app_tests = [
        ("excel",           "excel.exe"),
        ("vscode",          "code.exe"),
        ("vs code",         "code.exe"),
        ("vlc",             "vlc.exe"),
        ("calculator",      "calc.exe"),
        ("chrome",          "chrome.exe"),
        ("unknown app",     "unknownapp.exe"),   # passthrough
    ]
    for name, expected in app_tests:
        got = _resolve_app(name)
        ok  = "✓" if got == expected else "✗"
        print(f"  {ok}  _resolve_app({name!r:<20}) → {got!r}  (expected {expected!r})")

    print("\n── URL resolution ──────────────────────────────────────")
    url_tests = [
        ("youtube",             "https://youtube.com"),
        ("github.com",          "https://github.com"),
        ("amazon",              "https://amazon.in"),
        ("https://example.com", "https://example.com"),
        ("stack overflow",      "https://stackoverflow.com"),
    ]
    for name, expected in url_tests:
        got = _resolve_url(name)
        ok  = "✓" if got == expected else "✗"
        print(f"  {ok}  _resolve_url({name!r:<25}) → {got!r}  (expected {expected!r})")

    print()
    print("Resolution tests complete.")
    print()

    # Live action tests — comment out to skip.
    if "--live" in sys.argv:
        print("── Live action tests (--live flag detected) ────────────")
        open_application("notepad")
        time.sleep(1)
        open_website("youtube")
        time.sleep(1)
        search_google("best accessibility software")
        time.sleep(1)
        # type_text requires a focused window — skip in automated runs.
        print("Live tests dispatched.")