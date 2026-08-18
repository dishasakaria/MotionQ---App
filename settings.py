import json
import os
import tkinter as tk
from tkinter import messagebox
from pathlib import Path


APP_DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "MotionQ"
CONFIG_FILE = APP_DATA_DIR / "config.json"


def _ensure_app_data_dir():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    _ensure_app_data_dir()

    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config):
    _ensure_app_data_dir()

    temp_file = CONFIG_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    os.replace(temp_file, CONFIG_FILE)


def get_groq_api_key():
    config = load_config()
    return config.get("groq_api_key")


def set_groq_api_key(api_key):
    config = load_config()
    config["groq_api_key"] = api_key.strip()
    save_config(config)


def open_settings():
    window = tk.Tk()
    window.title("MotionQ Settings")
    window.geometry("500x260")
    window.resizable(False, False)

    title = tk.Label(
        window,
        text="MotionQ Settings",
        font=("Segoe UI", 18, "bold")
    )
    title.pack(pady=(20, 15))

    label = tk.Label(
        window,
        text="Groq API Key",
        font=("Segoe UI", 10)
    )
    label.pack(anchor="w", padx=40)

    key_var = tk.StringVar()
    existing_key = get_groq_api_key()

    if existing_key:
        key_var.set(existing_key)

    key_entry = tk.Entry(
        window,
        textvariable=key_var,
        show="*",
        width=52,
        font=("Segoe UI", 10)
    )
    key_entry.pack(padx=40, pady=(5, 10))

    status = tk.Label(
        window,
        text="Enter your Groq API key.",
        font=("Segoe UI", 10)
    )
    status.pack(pady=5)

    def save():
        key = key_var.get().strip()

        if not key:
            status.config(text="Please enter a Groq API key.")
            return

        try:
            set_groq_api_key(key)

            status.config(text="API key saved successfully.")
            window.update_idletasks()

            window.after(700, window.destroy)

        except Exception as e:
            status.config(text=f"Could not save API key: {e}")

    save_button = tk.Button(
        window,
        text="Save API Key",
        command=save,
        width=18,
        height=2
    )
    save_button.pack(pady=8)

    key_entry.focus_set()

    window.mainloop()


if __name__ == "__main__":
    open_settings()