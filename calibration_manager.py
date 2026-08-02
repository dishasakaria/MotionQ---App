import json
import os
import numpy as np
import cv2
import mediapipe as mp
import math
import time


class CalibrationManager:
    """Manages saving and loading calibration data for all features"""

    def __init__(self, filepath="calibration_data.json"):
        self.filepath = filepath
        self.data = self.load()

    # ─────────────────────────── persistence ───────────────────────────────

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.data, f, indent=2)

    def clear_all(self):
        self.data = {}
        self.save()

    def is_calibrated(self, feature):
        return feature in self.data

    # ─────────────────────── head calibration (unchanged) ──────────────────

    def set_head_calibration(self, neutral_y):
        self.data['head'] = {'neutral_y': float(neutral_y)}
        self.save()

    def get_head_calibration(self):
        return self.data.get('head', None)

    # ─────────────────────── smile calibration ─────────────────────────────

    # Minimum extra distance (% of neutral) required above neutral to
    # count as a smile.  This prevents yawning / talking from firing.
    _MIN_SMILE_THRESHOLD_PCT = 0.22   # 22 % above neutral

    def set_smile_calibration(self, neutral_intensity, smile_threshold):
        """
        Save smile calibration values.

        neutral_intensity : median lip-corner distance while relaxed
        smile_threshold   : extra distance needed above neutral.
                            Clamped to at least 22 % of neutral_intensity.
        """
        min_thresh = neutral_intensity * self._MIN_SMILE_THRESHOLD_PCT
        smile_threshold = max(float(smile_threshold), min_thresh)

        self.data['smile'] = {
            'neutral_intensity': float(neutral_intensity),
            'smile_threshold':   float(smile_threshold),
            'calibrated_at':     time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        self.save()

    def get_smile_calibration(self):
        return self.data.get('smile', None)

    # ─────────────────────── interactive calibration wizard ────────────────

    @staticmethod
    def _measure_lip_distance(cap, face_mesh, duration_sec=3.0,
                               left_corner=61, right_corner=291):
        """
        Collect lip-corner distances from the webcam for `duration_sec`
        seconds and return the median.  Returns None if no face is found.
        """
        samples = []
        deadline = time.time() + duration_sec

        while time.time() < deadline:
            ret, frame = cap.read()
            if not ret:
                continue
            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = face_mesh.process(rgb)

            if results.multi_face_landmarks:
                lm = results.multi_face_landmarks[0].landmark
                x1 = lm[left_corner].x * w
                y1 = lm[left_corner].y * h
                x2 = lm[right_corner].x * w
                y2 = lm[right_corner].y * h
                dist = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                samples.append(dist)

            # Draw countdown on frame
            remaining = max(0.0, deadline - time.time())
            cv2.putText(frame, f"Sampling: {remaining:.1f}s",
                        (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                        (0, 255, 255), 2)
            cv2.imshow("Smile Calibration", frame)
            cv2.waitKey(1)

        return float(np.median(samples)) if samples else None

    def run_smile_calibration_wizard(self, cap):
        """
        Interactive 3-step smile calibration wizard.

        Steps:
          1. Neutral face — collect baseline lip-corner distance
          2. Big smile  — collect smiling lip-corner distance
          3. Compute & save threshold with safety floor

        Returns True on success, False on failure / user abort.
        """
        mp_face_mesh = mp.solutions.face_mesh
        face_mesh = mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        win = "Smile Calibration"
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 640, 480)

        def _wait_for_key(message, key=32, timeout_sec=8):
            """Show message on webcam feed; wait for spacebar or timeout."""
            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                ret, frame = cap.read()
                if not ret:
                    continue
                frame = cv2.flip(frame, 1)
                # Draw instructions
                for i, line in enumerate(message.split('\n')):
                    cv2.putText(frame, line, (10, 50 + i * 35),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                                (255, 255, 0), 2)
                remaining = max(0.0, deadline - time.time())
                cv2.putText(frame, f"(SPACE to start | auto in {remaining:.0f}s)",
                            (10, frame.shape[0] - 15),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                            (200, 200, 200), 1)
                cv2.imshow(win, frame)
                if cv2.waitKey(1) & 0xFF == key:
                    return True
            return True   # auto-advance after timeout

        # ── Step 1 : neutral ────────────────────────────────────────────────
        print("\n📐 SMILE CALIBRATION WIZARD")
        print("=" * 45)
        _wait_for_key("STEP 1/2: Relax your face.\nKeep a NEUTRAL expression.\nPress SPACE when ready.")

        print("  ▸ Measuring neutral face … (3 s)")
        neutral_dist = self._measure_lip_distance(cap, face_mesh, duration_sec=3.0)

        if neutral_dist is None or neutral_dist < 5:
            print("  ❌ No face detected during neutral step. Calibration failed.")
            cv2.destroyWindow(win)
            face_mesh.close()
            return False

        print(f"  ✅ Neutral lip distance: {neutral_dist:.1f} px")

        # ── Step 2 : big smile ──────────────────────────────────────────────
        _wait_for_key("STEP 2/2: Give a BIG, NATURAL smile!\nHold it for 3 seconds.\nPress SPACE when ready.")

        print("  ▸ Measuring smile … (3 s)")
        smile_dist = self._measure_lip_distance(cap, face_mesh, duration_sec=3.0)

        if smile_dist is None or smile_dist < 5:
            print("  ❌ No face detected during smile step. Calibration failed.")
            cv2.destroyWindow(win)
            face_mesh.close()
            return False

        print(f"  ✅ Smile lip distance: {smile_dist:.1f} px")

        # ── Step 3 : compute threshold ──────────────────────────────────────
        raw_delta = smile_dist - neutral_dist
        min_delta = neutral_dist * self._MIN_SMILE_THRESHOLD_PCT

        if raw_delta < 5:
            # Measured smile was not distinguishable from neutral
            print("  ⚠️  Smile barely different from neutral.")
            print(f"      Falling back to minimum threshold: {min_delta:.1f} px")
            threshold = min_delta
        else:
            # Use 75 % of the measured delta so casual smiles still trigger,
            # but the bar is well above accidental triggers.
            threshold = max(raw_delta * 0.75, min_delta)

        self.set_smile_calibration(neutral_dist, threshold)

        trigger = neutral_dist + threshold
        print(f"\n  ✅ CALIBRATION SAVED")
        print(f"     Neutral distance : {neutral_dist:.1f} px")
        print(f"     Threshold        : {threshold:.1f} px")
        print(f"     Trigger at       : {trigger:.1f} px  (smile {threshold/neutral_dist*100:.0f}% bigger than neutral)")

        # ── Success screen ──────────────────────────────────────────────────
        deadline = time.time() + 2.5
        while time.time() < deadline:
            ret, frame = cap.read()
            if not ret:
                break
            frame = cv2.flip(frame, 1)
            cv2.putText(frame, "Calibration Complete!", (60, 200),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 100), 3)
            cv2.putText(frame, f"Trigger at: {trigger:.1f} px", (60, 260),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 0), 2)
            cv2.imshow(win, frame)
            cv2.waitKey(1)

        cv2.destroyWindow(win)
        face_mesh.close()
        return True