from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import cv2


class Recorder:
    """Independent consumer of the camera cache; encoding never blocks counting."""
    def __init__(self, camera, folder, fps=10, size=None, on_error=None):
        if fps <= 0:
            raise ValueError("Video fps phải > 0")
        self.camera, self.folder, self.fps, self.size = camera, Path(folder), fps, size
        self.on_error = on_error or (lambda message: None)
        self._stop, self._thread = Event(), None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="tray-recorder", daemon=True)
        self._thread.start()

    def _run(self):
        writer, segment = None, None
        try:
            while not self._stop.is_set():
                frame = self.camera.latest()
                if frame is not None:
                    now = datetime.now()
                    size = tuple(self.size) if self.size else (frame.shape[1], frame.shape[0])
                    key = (now.strftime("%Y%m%d_%H"), size)
                    if key != segment:
                        if writer:
                            writer.release()
                        self.folder.mkdir(parents=True, exist_ok=True)
                        path = self.folder / f"{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}.avi"
                        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), self.fps, size)
                        if not writer.isOpened():
                            raise IOError(f"Không mở được file video {path}")
                        segment = key
                    if (frame.shape[1], frame.shape[0]) != size:
                        frame = cv2.resize(frame, size)
                    writer.write(frame)
                self._stop.wait(1.0 / self.fps)
        except Exception as exc:
            self.on_error(str(exc))
        finally:
            if writer:
                writer.release()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise TimeoutError("Recorder chưa dừng")
