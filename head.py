import cv2
import mediapipe as mp
import pyautogui


def run_head_control(cap, stop_flag, calibration_manager):
    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(refine_landmarks=True)

    # Set smaller resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    SCROLL_THRESHOLD = 20
    SCROLL_AMOUNT = 30

    # Get saved calibration (must exist from startup)
    calibration = calibration_manager.get_head_calibration()

    if not calibration:
        print("❌ ERROR: Head not calibrated! This should not happen.")
        return

    neutral_y = calibration['neutral_y']
    print(f"✅ Using saved head calibration: neutral_y={neutral_y:.1f}")

    print("Head movement scroll active!")

    cv2.namedWindow("Head Scroll Control - Q to Quit", cv2.WINDOW_NORMAL)
    cv2.moveWindow("Head Scroll Control - Q to Quit", 100, 100)
    cv2.resizeWindow("Head Scroll Control - Q to Quit", 640, 480)

    while not stop_flag.is_set():
        ret, frame = cap.read()
        if not ret:
            break

        frame = cv2.flip(frame, 1)
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb_frame)

        if results.multi_face_landmarks:
            face_landmarks = results.multi_face_landmarks[0]
            h, w, _ = frame.shape
            nose = face_landmarks.landmark[1]
            nose_y = int(nose.y * h)

            dy = nose_y - neutral_y
            status = "Neutral"

            if dy > SCROLL_THRESHOLD:
                pyautogui.scroll(-SCROLL_AMOUNT)
                status = "Scrolling Down"
            elif dy < -SCROLL_THRESHOLD:
                pyautogui.scroll(SCROLL_AMOUNT)
                status = "Scrolling Up"

            cv2.putText(frame, f"dy: {int(dy)} {status}", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        else:
            cv2.putText(frame, "No face detected.", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        cv2.imshow("Head Scroll Control - Q to Quit", frame)

        key = cv2.waitKey(1)
        if key == ord('q'):
            break

    cv2.destroyAllWindows()
