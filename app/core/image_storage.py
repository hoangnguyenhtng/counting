
from datetime import datetime
import os
from typing import Optional

import cv2
import numpy as np

from app.paths import get_abs_path


def save_captured_image(
    image: np.ndarray,
    captured_at: Optional[datetime] = None,
    root_dir: Optional[str] = None,
) -> str:
    """Save a raw capture under a date folder and return its absolute path."""
    if image is None or image.size == 0:
        raise ValueError("Cannot save an empty captured image.")

    captured_at = captured_at or datetime.now()
    root_dir = root_dir or get_abs_path("ImageCapture")
    date_folder = os.path.join(root_dir, captured_at.strftime("%Y_%m_%d"))
    os.makedirs(date_folder, exist_ok=True)

    filename = f"Capture_{captured_at.strftime('%H_%M_%S_%f')}.jpg"
    image_path = os.path.join(date_folder, filename)
    if not cv2.imwrite(image_path, image):
        raise IOError(f"Failed to save captured image: {image_path}")

    return os.path.abspath(image_path)
