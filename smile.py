import cv2
import pyautogui
import time
import mediapipe as mp
import math


def run_smile_control(cap, stop_flag, calibration_manager, eye_tracker=None):
    """
    Smile detection using lip-corner distance (MediaPipe).

    On a confirmed smile:
      • Right-click (context menu) at the current cursor position.

    Pass an EyeTrackingMouse instance as eye_tracker to keep
    eye tracking active while smile mode is running.

    Calibration is loaded from calibration_manager.  If the user has
    not yet calibrated, the wizard is run automatically before the
    detection loop starts.
    """

    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

    # ── Ensure calibration exists ───────────────────────────────────────────
    calibration = calibration_manager.get_smile_calibration()
    if not calibration:
        print("⚠️  No smile calibration found — launching calibration wizard…")
        success = calibration_manager.run_smile_calibration_wizard(cap)
        if not success:
            print("❌ Calibration failed or was cancelled. Smile mode aborted.")
            return
        calibration = calibration_manager.get_smile_calibration()

    neutral_distance = calibration['neutral_intensity']
    smile_threshold  = calibration['smile_threshold']
    trigger_distance = neutral_distance + smile_threshold
    calibrated_at    = calibration.get('calibrated_at', 'unknown')

    # ── MediaPipe setup ─────────────────────────────────────────────────────
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Lip-corner landmark indices (MediaPipe 468-point mesh)
    LEFT_LIP_CORNER  = 61
    RIGHT_LIP_CORNER = 291

    # ── State ───────────────────────────────────────────────────────────────
    last_action_time  = 0.0
    action_cooldown   = 2.0          # seconds between right-clicks
    smile_hold_frames = 0            # consecutive frames above trigger
    HOLD_REQUIRED     = 4            # frames smile must be held before firing
    eye_status = "👁️ + 😊 MODE" if eye_tracker else "😊 SMILE MODE"

    print("\n" + "=" * 55)
    print(f"😊 SMILE DETECTION ACTIVE  "
          f"{'(Eye tracking also ON)' if eye_tracker else ''}")
    print("=" * 55)
    print(f"   Calibrated at   : {calibrated_at}")
    print(f"   Neutral distance: {neutral_distance:.1f} px")
    print(f"   Smile threshold : {smile_threshold:.1f} px  "
          f"(+{smile_threshold/neutral_distance*100:.0f}% above neutral)")
    print(f"   Trigger at      : {trigger_distance:.1f} px")
    print(f"   Action          : Right-click (context menu)")
    print("   Press Q to quit")
    print("=" * 55 + "\n")

    cv2.namedWindow('Smile Control — Q to Quit', cv2.WINDOW_NORMAL)
    cv2.moveWindow('Smile Control — Q to Quit', 100, 100)
    cv2.resizeWindow('Smile Control — Q to Quit', 640, 480)

    # ── Main loop ───────────────────────────────────────────────────────────
    while not stop_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)

        # Eye tracking runs on the same frame when active
        if eye_tracker is not None:
            frame = eye_tracker.process_frame(frame)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results   = face_mesh.process(rgb_frame)

        h, w, _ = frame.shape
        status           = "No face detected"
        status_color     = (0, 0, 255)
        current_distance = 0.0

        if results.multi_face_landmarks:
            lm = results.multi_face_landmarks[0].landmark

            lc = lm[LEFT_LIP_CORNER]
            rc = lm[RIGHT_LIP_CORNER]
            x1, y1 = int(lc.x * w), int(lc.y * h)
            x2, y2 = int(rc.x * w), int(rc.y * h)

            current_distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

            # Draw lip-corner markers and line
            cv2.circle(frame, (x1, y1), 5, (0, 255, 255), -1)
            cv2.circle(frame, (x2, y2), 5, (0, 255, 255), -1)
            cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 255), 2)

            # HUD — distance readout
            cv2.putText(frame, f"Dist: {current_distance:.1f}  |  Trigger: {trigger_distance:.1f}",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 0), 2)

            # Progress bar toward trigger
            progress  = min(100.0, (current_distance / trigger_distance) * 100.0)
            bar_fill  = int((progress / 100.0) * 300)
            bar_color = (0, 200, 0) if progress < 85 else (0, 255, 0) if progress < 100 else (0, 140, 255)
            cv2.rectangle(frame, (10, 45), (310, 65), (80, 80, 80), 2)
            cv2.rectangle(frame, (10, 45), (10 + bar_fill, 65), bar_color, -1)
            cv2.putText(frame, f"{progress:.0f}%",
                        (318, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # ── Smile detection logic ───────────────────────────────────────
            now = time.time()
            cooldown_remaining = action_cooldown - (now - last_action_time)

            if current_distance > trigger_distance:
                smile_hold_frames += 1

                if smile_hold_frames >= HOLD_REQUIRED:
                    if cooldown_remaining <= 0:
                        # ── FIRE: right-click context menu ──────────────────
                        print(f"😊 SMILE CONFIRMED (dist={current_distance:.1f})  →  Right-click!")
                        try:
                            pyautogui.click(button='right')
                            last_action_time  = now
                            smile_hold_frames = 0
                            status       = "✅ RIGHT-CLICK!"
                            status_color = (0, 255, 0)
                            print("✅ Context menu opened.\n")
                        except Exception as e:
                            print(f"❌ Right-click error: {e}")
                            status       = "❌ Click error"
                            status_color = (0, 0, 255)
                    else:
                        # Smile confirmed but cooling down
                        status       = f"Hold… cooldown {cooldown_remaining:.1f}s"
                        status_color = (255, 200, 0)
                else:
                    # Smile detected but not held long enough yet
                    frames_left  = HOLD_REQUIRED - smile_hold_frames
                    status       = f"Smile bigger! ({frames_left} frames…)"
                    status_color = (0, 165, 255)

            elif current_distance > neutral_distance + smile_threshold * 0.5:
                smile_hold_frames = max(0, smile_hold_frames - 1)
                status       = "Almost — smile wider!"
                status_color = (0, 165, 255)
            else:
                smile_hold_frames = 0
                if cooldown_remaining > 0:
                    status       = f"Cooldown {cooldown_remaining:.1f}s"
                    status_color = (255, 220, 0)
                else:
                    status       = "Neutral — SMILE to right-click"
                    status_color = (160, 160, 160)

        else:
            smile_hold_frames = 0

        # ── Mode label (top-right) ──────────────────────────────────────────
        cv2.putText(frame, eye_status, (w - 230, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 128) if eye_tracker else (200, 200, 200), 2)

        # ── Status text ─────────────────────────────────────────────────────
        cv2.putText(frame, status, (10, 140),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, status_color, 2)

        # ── Recalibrate hint ────────────────────────────────────────────────
        cv2.putText(frame, "Press R to recalibrate  |  Q to quit",
                    (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (140, 140, 140), 1)

        cv2.imshow('Smile Control — Q to Quit', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            # Re-run calibration wizard on demand
            print("\n🔄 Recalibrating smile…")
            face_mesh.close()
            success = calibration_manager.run_smile_calibration_wizard(cap)
            if success:
                calibration      = calibration_manager.get_smile_calibration()
                neutral_distance = calibration['neutral_intensity']
                smile_threshold  = calibration['smile_threshold']
                trigger_distance = neutral_distance + smile_threshold
                calibrated_at    = calibration.get('calibrated_at', 'unknown')
                print(f"  ✅ New trigger at {trigger_distance:.1f} px\n")
            else:
                print("  ⚠️  Recalibration cancelled — keeping previous values.")
            # Re-open face mesh
            face_mesh = mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )

    face_mesh.close()
    cv2.destroyAllWindows()
    print("\n✅ Smile detection stopped.\n")