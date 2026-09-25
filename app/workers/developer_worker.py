"""Background folder inspection used by the Developer tab."""
from pathlib import Path
import threading

from PyQt5.QtCore import QThread, pyqtSignal

from app.core.detection import detect_tape
from app.core.folder_crop import read_image


class DeveloperWorker(QThread):
    """Run the production detector over a fixed list of image files."""

    result_ready = pyqtSignal(str, object, int, int)
    image_error = pyqtSignal(str, str, int, int)
    phase_changed = pyqtSignal(str, int, int)
    batch_completed = pyqtSignal(int, int, int, int, bool)

    def __init__(self, image_paths, config, model_pool, delay_ms=0, parent=None):
        super().__init__(parent)
        self.image_paths = [Path(path) for path in image_paths]
        self.config = config
        self.model_pool = model_pool
        self.delay_ms = max(0, int(delay_ms))
        self._stop_event = threading.Event()

    def run(self):
        processed = ok_count = ng_count = error_count = 0
        total = len(self.image_paths)

        for index, image_path in enumerate(self.image_paths, start=1):
            if self._stop_event.is_set():
                break

            self.phase_changed.emit("CAPTURE", index, total)
            image = read_image(image_path)
            if image is None:
                error_count += 1
                processed += 1
                self.image_error.emit(
                    str(image_path), "Image could not be read", index, total
                )
            else:
                try:
                    self.phase_changed.emit("DETECT", index, total)
                    result = detect_tape(image, self.config, self.model_pool)
                except Exception as exc:
                    error_count += 1
                    processed += 1
                    self.image_error.emit(str(image_path), str(exc), index, total)
                else:
                    processed += 1
                    if result.ret == "PASS":
                        ok_count += 1
                    else:
                        ng_count += 1
                    self.result_ready.emit(str(image_path), result, index, total)

            # Event.wait keeps a long simulation delay immediately interruptible.
            if index < total and self.delay_ms:
                self.phase_changed.emit("DELAY", index, total)
                if self._stop_event.wait(self.delay_ms / 1000.0):
                    break

        self.batch_completed.emit(
            processed, ok_count, ng_count, error_count, self._stop_event.is_set()
        )

    def request_stop(self):
        self._stop_event.set()

    def stop(self, timeout_ms=5000):
        self.request_stop()
        return self.wait(timeout_ms)
