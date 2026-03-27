"""
virtual_keyboard.py — Floating on-screen keyboard for hands-free input.
Designed to work alongside Eye Tracking mode so users can click keys with their eyes.
"""

import threading
import time
import tkinter as tk
from tkinter import font as tkfont

try:
    from pynput.keyboard import Key, Controller as KbController
    PYNPUT_AVAILABLE = True
except ImportError:
    PYNPUT_AVAILABLE = False
    print("⚠️  pynput not installed — using pyautogui fallback for keyboard")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False


# ── Key layout ─────────────────────────────────────────────────────────────────

ROWS = [
    ['ESC', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12'],
    ['`',  '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-',  '=',  'BKSP'],
    ['TAB', 'q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', '\\'],
    ['CAPS', 'a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'", 'ENTER'],
    ['SHIFT', 'z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/', 'SHIFT'],
    ['CTRL', 'WIN', 'ALT', 'SPACE', 'ALT', 'CTRL', '←', '↑', '↓', '→'],
]

SHIFT_MAP = {
    '`': '~', '1': '!', '2': '@', '3': '#', '4': '$', '5': '%',
    '6': '^', '7': '&', '8': '*', '9': '(', '0': ')', '-': '_',
    '=': '+', '[': '{', ']': '}', '\\': '|', ';': ':', "'": '"',
    ',': '<', '.': '>', '/': '?',
}

# (width_units, display_label_override)
KEY_META = {
    'ESC':   (2.0, 'Esc'),
    'BKSP':  (3.2, '⌫'),
    'TAB':   (2.8, 'Tab'),
    'CAPS':  (3.2, 'Caps'),
    'ENTER': (3.5, '↵ Enter'),
    'SHIFT': (4.5, '⇧ Shift'),
    'SPACE': (11,  'Space'),
    'CTRL':  (2.5, 'Ctrl'),
    'WIN':   (2.0, '❖'),
    'ALT':   (2.2, 'Alt'),
    '←':     (1.8, '←'),
    '→':     (1.8, '→'),
    '↑':     (1.8, '↑'),
    '↓':     (1.8, '↓'),
    'F1': (1.5, 'F1'), 'F2': (1.5, 'F2'), 'F3': (1.5, 'F3'), 'F4': (1.5, 'F4'),
    'F5': (1.5, 'F5'), 'F6': (1.5, 'F6'), 'F7': (1.5, 'F7'), 'F8': (1.5, 'F8'),
    'F9': (1.5, 'F9'), 'F10': (1.5, 'F10'), 'F11': (1.7, 'F11'), 'F12': (1.7, 'F12'),
}

# Catppuccin Mocha colour palette
THEME = {
    'bg':          '#1e1e2e',
    'surface':     '#313244',
    'overlay':     '#45475a',
    'special_key': '#585b70',
    'fg':          '#cdd6f4',
    'accent':      '#89b4fa',
    'accent_fg':   '#1e1e2e',
    'red':         '#f38ba8',
    'green':       '#a6e3a1',
    'yellow':      '#f9e2af',
    'titlebar':    '#181825',
}


class VirtualKeyboard:
    """Floating, draggable on-screen keyboard. Call build(stop_flag) to show."""

    def __init__(self):
        self.shift_active = False
        self.caps_active  = False
        self._kb = KbController() if PYNPUT_AVAILABLE else None
        self._buttons: list[tuple[tk.Button, str]] = []
        self._drag_x = self._drag_y = 0
        self.root = None

    # ── Input helpers ───────────────────────────────────────────────────────────

    def _type_char(self, char: str):
        if PYNPUT_AVAILABLE and self._kb:
            self._kb.type(char)
        elif PYAUTOGUI_AVAILABLE:
            pyautogui.write(char, interval=0.02)

    def _press_special(self, name: str):
        pynput_map = {
            'BKSP':  Key.backspace, 'ENTER': Key.enter,  'TAB':  Key.tab,
            'ESC':   Key.esc,       'SPACE': Key.space,   'CAPS': Key.caps_lock,
            '←':     Key.left,      '→':     Key.right,   '↑':   Key.up,
            '↓':     Key.down,
            'F1': Key.f1, 'F2': Key.f2, 'F3': Key.f3,  'F4': Key.f4,
            'F5': Key.f5, 'F6': Key.f6, 'F7': Key.f7,  'F8': Key.f8,
            'F9': Key.f9, 'F10': Key.f10, 'F11': Key.f11, 'F12': Key.f12,
        }
        pyag_map = {
            'BKSP': 'backspace', 'ENTER': 'enter',  'TAB': 'tab',
            'ESC':  'escape',    'SPACE': 'space',
            '←': 'left', '→': 'right', '↑': 'up', '↓': 'down',
            'F1':'f1','F2':'f2','F3':'f3','F4':'f4','F5':'f5','F6':'f6',
            'F7':'f7','F8':'f8','F9':'f9','F10':'f10','F11':'f11','F12':'f12',
        }
        if PYNPUT_AVAILABLE and self._kb and name in pynput_map:
            self._kb.press(pynput_map[name])
            self._kb.release(pynput_map[name])
        elif PYAUTOGUI_AVAILABLE and name in pyag_map:
            pyautogui.press(pyag_map[name])

    # ── Key click handler ───────────────────────────────────────────────────────

    def _on_click(self, key: str):
        # Modifier toggles
        if key == 'SHIFT':
            self.shift_active = not self.shift_active
            self._refresh_labels()
            return
        if key == 'CAPS':
            self.caps_active = not self.caps_active
            self._press_special('CAPS')
            self._refresh_labels()
            return
        if key in ('CTRL', 'ALT', 'WIN'):
            return  # handled by combos in voice commands, skip standalone

        # Special keys with no character
        SPECIAL = {'BKSP','ENTER','TAB','ESC','SPACE','←','→','↑','↓',
                   'F1','F2','F3','F4','F5','F6','F7','F8','F9','F10','F11','F12'}
        if key in SPECIAL:
            self._press_special(key)
            return

        # Regular character
        char = key
        if self.shift_active:
            char = SHIFT_MAP.get(key, key.upper() if key.isalpha() else key)
            self.shift_active = False
            self._refresh_labels()
        elif self.caps_active and key.isalpha():
            char = key.upper()

        self._type_char(char)

    # ── Label updater ───────────────────────────────────────────────────────────

    def _refresh_labels(self):
        for btn, orig in self._buttons:
            if len(orig) == 1 and orig.isalpha():
                upper = self.caps_active ^ self.shift_active
                btn.config(text=orig.upper() if upper else orig.lower())
            elif len(orig) == 1 and orig in SHIFT_MAP and self.shift_active:
                btn.config(text=SHIFT_MAP[orig])
            elif len(orig) == 1 and orig in SHIFT_MAP:
                btn.config(text=orig)
            # Highlight shift key
            if orig == 'SHIFT':
                bg = THEME['green'] if self.shift_active else THEME['special_key']
                btn.config(bg=bg)
            if orig == 'CAPS':
                bg = THEME['yellow'] if self.caps_active else THEME['special_key']
                btn.config(bg=bg)

    # ── Drag support ────────────────────────────────────────────────────────────

    def _drag_start(self, e):
        self._drag_x, self._drag_y = e.x, e.y

    def _drag_move(self, e):
        dx = e.x - self._drag_x
        dy = e.y - self._drag_y
        x  = self.root.winfo_x() + dx
        y  = self.root.winfo_y() + dy
        self.root.geometry(f'+{x}+{y}')

    # ── Build UI ────────────────────────────────────────────────────────────────

    def build(self, stop_flag: threading.Event):
        """Create and run the keyboard window. Blocks until stop_flag is set."""
        self.root = tk.Tk()
        self.root.title('Virtual Keyboard')
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        self.root.configure(bg=THEME['bg'])

        # Position near bottom of screen
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f'+0+{sh - 340}')

        # ── Title / drag bar ────────────────────────────────────────────────
        title = tk.Frame(self.root, bg=THEME['titlebar'], height=28, cursor='fleur')
        title.pack(fill='x')
        title.bind('<ButtonPress-1>', self._drag_start)
        title.bind('<B1-Motion>',     self._drag_move)

        tk.Label(
            title,
            text='⌨   Virtual Keyboard  —  drag to reposition',
            bg=THEME['titlebar'], fg=THEME['fg'],
            font=('Segoe UI', 9),
        ).pack(side='left', padx=10)

        tk.Button(
            title, text='✕',
            bg=THEME['red'], fg='white',
            relief='flat', font=('Segoe UI', 10, 'bold'),
            cursor='hand2', width=3,
            command=stop_flag.set,
        ).pack(side='right')

        # ── Key rows ────────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=THEME['bg'], padx=8, pady=8)
        body.pack()

        UNIT = 42  # pixels per 1.0 width unit

        for row in ROWS:
            row_frame = tk.Frame(body, bg=THEME['bg'])
            row_frame.pack(pady=2, anchor='w')

            for key in row:
                w_units, label = KEY_META.get(key, (1.0, None))
                if label is None:
                    label = key.upper() if (self.caps_active) else key.lower() \
                        if key.isalpha() else key

                px_w = max(int(UNIT * w_units) - 4, 28)

                is_special = key in KEY_META
                is_modifier = key in ('SHIFT', 'CAPS', 'CTRL', 'ALT', 'WIN')
                bg = THEME['special_key'] if is_modifier else \
                     THEME['overlay']     if is_special  else \
                     THEME['surface']

                btn = tk.Button(
                    row_frame,
                    text=label,
                    width=0,
                    font=('Consolas', 10, 'bold'),
                    bg=bg, fg=THEME['fg'],
                    activebackground=THEME['accent'],
                    activeforeground=THEME['accent_fg'],
                    relief='flat', bd=0, padx=4, pady=6,
                    cursor='hand2',
                    command=lambda k=key: self._on_click(k),
                )
                btn.config(width=max(int(w_units * 3.8), 2))
                btn.pack(side='left', padx=2)
                self._buttons.append((btn, key))

        # ── Indicator row ───────────────────────────────────────────────────
        indicator = tk.Frame(self.root, bg=THEME['titlebar'])
        indicator.pack(fill='x')
        self._caps_label  = tk.Label(indicator, text='Caps OFF', bg=THEME['titlebar'],
                                     fg='#6c7086', font=('Segoe UI', 8))
        self._shift_label = tk.Label(indicator, text='Shift OFF', bg=THEME['titlebar'],
                                     fg='#6c7086', font=('Segoe UI', 8))
        self._caps_label.pack(side='left', padx=10, pady=2)
        self._shift_label.pack(side='left', padx=10)
        tk.Label(indicator, text='Click keys or use Eye Tracking to navigate',
                 bg=THEME['titlebar'], fg='#6c7086',
                 font=('Segoe UI', 8)).pack(side='right', padx=10)

        # ── Stop flag polling ───────────────────────────────────────────────
        def _poll():
            self._caps_label.config(
                text=f'Caps {"ON ✓" if self.caps_active else "OFF"}',
                fg=THEME['yellow'] if self.caps_active else '#6c7086'
            )
            self._shift_label.config(
                text=f'Shift {"ON ✓" if self.shift_active else "OFF"}',
                fg=THEME['green'] if self.shift_active else '#6c7086'
            )
            if stop_flag.is_set():
                self.root.destroy()
                return
            self.root.after(150, _poll)

        self.root.after(150, _poll)
        self.root.mainloop()


# ── Entry point called from mainController ─────────────────────────────────────

def run_keyboard_mode(cap, stop_flag: threading.Event, eye_tracker=None):
    """
    Show virtual keyboard.
    Optionally also starts eye tracking so the user can click keys with their eyes.
    """
    print('\n⌨️  Virtual Keyboard mode active')
    print('   Use eye tracking to hover + blink-click on keys.')
    print('   Say "keyboard" again or press ✕ to close.\n')

    # Run eye tracking in background so keys can be clicked hands-free
    if eye_tracker is not None:
        eye_stop = threading.Event()

        def _eye_thread():
            try:
                eye_tracker.run_modular(cap, eye_stop)
            except Exception as e:
                print(f'Eye tracker error in keyboard mode: {e}')

        t = threading.Thread(target=_eye_thread, daemon=True)
        t.start()
    else:
        eye_stop = None
        t = None

    # Build keyboard (blocks until stop_flag or ✕ clicked)
    kb = VirtualKeyboard()

    # If main stop_flag fires, also stop eye tracker
    def _watch():
        while not stop_flag.is_set():
            time.sleep(0.2)
        if eye_stop:
            eye_stop.set()

    threading.Thread(target=_watch, daemon=True).start()

    kb.build(stop_flag)

    # Cleanup
    if eye_stop:
        eye_stop.set()
    if t:
        t.join(timeout=2)

    print('⌨️  Virtual Keyboard closed.\n')