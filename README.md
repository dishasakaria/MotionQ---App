# 🎯 Multi-Feature Face Control System

An OpenCV-based system that uses facial features and voice commands to control your computer. Switch between three powerful features using simple voice commands!

## ✨ Features

### 👁️ Eye Tracking Mode
- **Activation**: Say "EYE" or "I"
- Move your cursor by looking at different parts of the screen
- Double blink to perform a double-click
- Perfect for hands-free navigation

### 😊 Smile Detection Mode
- **Activation**: Say "SMILE"
- Smile to switch between browser tabs
- Uses Ctrl+Shift+Tab hotkey
- Automatic calibration on startup

### 🔄 Head Movement Mode
- **Activation**: Say "HEAD"
- Tilt your head up to scroll up
- Tilt your head down to scroll down
- Calibration required for neutral position

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Note**: Installing PyAudio on Windows may require:
```bash
pip install pipwin
pipwin install pyaudio
```

### 2. Run the System

```bash
python mainController.py
```

### 3. Use Voice Commands

Once running, simply say:
- **"EYE"** - Activate eye tracking
- **"SMILE"** - Activate smile detection
- **"HEAD"** - Activate head movement

## 🎮 Controls

### Voice Commands
- Say feature names to activate them
- System continuously listens for commands

### Keyboard Shortcuts
- **Q**: Stop current feature (in OpenCV window)
- **Ctrl+C**: Exit entire program
- **Space**: Calibration (feature-specific)
- **C**: Recalibrate (eye tracking)
- **R**: Recalibrate (head movement)

## 📋 System Requirements

- Python 3.8+
- Webcam
- Microphone
- Internet connection (for Google Speech Recognition API)

## 🔧 File Structure

```
Features/
├── mainController.py    # Main voice-controlled system
├── eyefeature.py       # Eye tracking implementation
├── smile.py            # Smile detection implementation
├── head.py             # Head movement implementation
└── requirements.txt    # Python dependencies
```

## 💡 Tips

1. **Lighting**: Ensure good lighting for better face detection
2. **Microphone**: Speak clearly and at normal volume
3. **Calibration**: Follow calibration prompts for best accuracy
4. **Position**: Sit 1-2 feet from the camera in the center of the frame

## ⚠️ Troubleshooting

### Webcam Issues
- Ensure no other application is using the webcam
- Try changing camera index in code (0 to 1 or 2)

### Microphone Issues
- Check microphone permissions in system settings
- Test microphone in other applications first

### Voice Recognition Not Working
- Requires internet connection for Google Speech Recognition
- Speak clearly and minimize background noise

### Performance Issues
- Close other resource-intensive applications
- Reduce camera resolution if needed

## 🛠️ Customization

### Adjust Eye Tracking Sensitivity
In `eyefeature.py`:
```python
eye = EyeTrackingMouse(sensitivity=2.5)  # Default: 2.5
```

### Adjust Smile Cooldown
In `smile.py`:
```python
smile_cooldown = 3  # Seconds between smile detections
```

### Adjust Head Movement Threshold
In `head.py`:
```python
SCROLL_THRESHOLD = 20  # Pixels of head movement
SCROLL_AMOUNT = 30     # Scroll distance
```

## 📝 How It Works

1. **Voice Recognition**: Uses Google Speech Recognition API
2. **Face Detection**: MediaPipe for eye/head tracking, Haar Cascades for smile
3. **Mouse Control**: PyAutoGUI for cursor movement and clicks
4. **Threading**: Smooth feature switching without blocking

## 🤝 Contributing

Feel free to fork, modify, and improve this project!

## 📄 License

This project is open source and available for personal and educational use.

---

**Created with ❤️ using OpenCV, MediaPipe, and Python**
