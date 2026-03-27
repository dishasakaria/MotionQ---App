import json
import os
import numpy as np

class CalibrationManager:
    """Manages saving and loading calibration data for all features"""

    def __init__(self, filepath="calibration_data.json"):
        self.filepath = filepath
        self.data = self.load()

    def load(self):
        """Load calibration data from file"""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save(self):
        """Save calibration data to file"""
        with open(self.filepath, 'w') as f:
            json.dump(self.data, f, indent=2)

    def set_smile_calibration(self, neutral_intensity, smile_threshold):
        """Save smile calibration values"""
        self.data['smile'] = {
            'neutral_intensity': float(neutral_intensity),
            'smile_threshold': float(smile_threshold)
        }
        self.save()

    def get_smile_calibration(self):
        """Get smile calibration values"""
        return self.data.get('smile', None)

    def set_head_calibration(self, neutral_y):
        """Save head tilt calibration"""
        self.data['head'] = {
            'neutral_y': float(neutral_y)
        }
        self.save()

    def get_head_calibration(self):
        """Get head calibration values"""
        return self.data.get('head', None)

    def clear_all(self):
        """Clear all calibration data"""
        self.data = {}
        self.save()

    def is_calibrated(self, feature):
        """Check if a feature is calibrated"""
        return feature in self.data