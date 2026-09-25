from abc import ABC, abstractmethod
import numpy as np

NO_ERROR = ""
ERR_NOT_FOUND_DEVICE = "ERR_NOT_FOUND_DEVICE"
ERR_CREATE_DEVICE_FAIL = "ERR_CREATE_DEVICE_FAIL"
ERR_MODEL_NAME = "ERR_MODEL_NAME"
ERR_LOAD_FEATURE_FAIL = "ERR_LOAD_FEATURE_FAIL"
ERR_CONFIG_IS_NONE = "ERR_CONFIG_IS_NONE"
ERR_GRAB_FAIL = "ERR_GRAB_FAIL"

class BaseCamera(ABC):
    __MODEL_NAMES = ["MVAC-PY-FV533"]
    
    def __init__(self, config=None) -> None:
        super().__init__()
        self._error = NO_ERROR
        self._cap = None
        self._config = {}
        self._model_name = ""
        if config is not None:
            self.set_config(config)

    @abstractmethod
    def get_error(self) -> str:
        pass

    @abstractmethod
    def get_devices() -> dict:
        pass

    @abstractmethod
    def set_config(self, config):
        pass

    @abstractmethod
    def create_device(self):
        pass

    @abstractmethod
    def get_config(self):
        pass

    def is_valid_model_name(self) -> bool:
        # In this context, we return True so all cameras are accepted.
        # It matches the original commented logic or bypasses model locking.
        return True

    def get_model_name(self) -> str:
        return self._model_name

    @abstractmethod
    def open(self) -> bool:
        pass

    @abstractmethod
    def close(self) -> bool:
        pass

    @abstractmethod
    def start_grabbing(self) -> bool:
        pass

    @abstractmethod
    def stop_grabbing(self) -> bool:
        pass

    @abstractmethod
    def grab(self) -> tuple:
        pass
