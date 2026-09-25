import threading
from ultralytics import YOLO
import numpy as np

class ModelPool:
    def __init__(self):
        self._models = {}
        self._lock = threading.Lock()

    def get(self, model_path: str) -> YOLO:
        with self._lock:
            if model_path not in self._models:
                model = YOLO(model_path)
                
                model.predict(source=np.zeros((640, 640, 3), dtype=np.uint8), verbose=False)
                self._models[model_path] = model
            return self._models[model_path]

    def release(self, model_path: str):
        with self._lock:
            if model_path in self._models:
                del self._models[model_path]

    def clear(self):
        with self._lock:
            self._models.clear()

model_pool = ModelPool()
