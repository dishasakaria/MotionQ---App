import tkinter as tk
import runpy
import os
from pathlib import Path

import sounddevice as sd

from settings import get_groq_api_key


BASE_DIR = Path(__file__).resolve().parent
CALIBRATION_FILE = Path(os.environ["LOCALAPPDATA"]) / "MotionQ" / "calibration_data.json"


class MotionQLauncher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MotionQ")
        self.root.geometry("520x430")
        self.root.resizable(False, False)

        title = tk.Label(
            self.root,
            text="MotionQ",
            font=("Segoe UI", 26, "bold")
        )
        title.pack(pady=(30, 5))

        subtitle = tk.Label(
            self.root,
            text="Hands-free computer control",
            font=("Segoe UI", 11)
        )
        subtitle.pack(pady=(0, 25))

        self.status_frame = tk.Frame(self.root)
        self.status_frame.pack(pady=5)

        self.camera_status = self.create_status_row("Camera")
        self.microphone_status = self.create_status_row("Microphone")
        self.calibration_status = self.create_status_row("Calibration")
        self.voice_status = self.create_status_row("Cloud Voice")

        self.status = tk.Label(
            self.root,
            text="Checking system...",
            font=("Segoe UI", 10)
        )
        self.status.pack(pady=(20, 10))

        self.start_button = tk.Button(
            self.root,
            text="OPEN MOTIONQ",
            font=("Segoe UI", 11, "bold"),
            width=20,
            height=2,
            command=self.start_motionq,
            state="disabled"
        )
        self.start_button.pack(pady=10)

        settings_button = tk.Button(
            self.root,
            text="Settings",
            width=15,
            command=self.open_settings
        )
        settings_button.pack(pady=5)

        quit_button = tk.Button(
            self.root,
            text="Exit",
            width=15,
            command=self.root.destroy
        )
        quit_button.pack(pady=5)

        self.check_system()

    def create_status_row(self, name):
        row = tk.Frame(self.status_frame)
        row.pack(fill="x", pady=5)

        label = tk.Label(
            row,
            text=name,
            width=16,
            anchor="w",
            font=("Segoe UI", 10)
        )
        label.pack(side="left")

        status = tk.Label(
            row,
            text="Checking...",
            width=18,
            anchor="w",
            font=("Segoe UI", 10)
        )
        status.pack(side="left")

        return status

    def set_status(self, widget, text):
        widget.config(text=text)

    def check_camera(self):
        return True

    def check_microphone(self):
        try:
            devices = sd.query_devices()

            for device in devices:
                if device["max_input_channels"] > 0:
                    return True

            return False

        except Exception:
            return False

    def check_calibration(self):
        return CALIBRATION_FILE.exists()

    def check_voice(self):
        return bool(get_groq_api_key())

    def check_system(self):
        self.status.config(text="Checking system...")

        camera_ok = self.check_camera()
        microphone_ok = self.check_microphone()
        calibration_ok = self.check_calibration()
        voice_ok = self.check_voice()

        self.set_status(
            self.camera_status,
            "✓ Ready" if camera_ok else "✗ Unavailable"
        )

        self.set_status(
            self.microphone_status,
            "✓ Ready" if microphone_ok else "✗ Unavailable"
        )

        self.set_status(
            self.calibration_status,
            "✓ Complete" if calibration_ok else "○ Required"
        )

        self.set_status(
            self.voice_status,
            "✓ Ready" if voice_ok else "○ Not configured"
        )

        if camera_ok and microphone_ok:
            self.status.config(text="MotionQ is ready.")
            self.start_button.config(state="normal")
        else:
            self.status.config(
                text="Please connect the required camera and microphone."
            )

    def open_settings(self):
        from settings import open_settings

        open_settings()
        self.check_system()

    def start_motionq(self):
        self.start_button.config(state="disabled")
        self.status.config(text="Starting MotionQ...")
        self.root.update_idletasks()

        try:
            self.root.destroy()

            runpy.run_module(
                "mainController",
                run_name="__main__"
            )

        except Exception as e:
            print(f"Failed to start MotionQ: {e}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    MotionQLauncher().run()