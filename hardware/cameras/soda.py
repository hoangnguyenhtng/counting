from hardware.cameras.base_camera import *
from pypylon import pylon, genicam
import cv2
import os


class SODA(BaseCamera):
    """Basler camera driver via pypylon (Pylon SDK)."""

    def __init__(self, config=None) -> None:
        self._converter = None
        super().__init__(config=config)

    def set_config(self, config):
        self._config = config
        self.create_device()

    def get_config(self):
        return self._config

    def get_error(self) -> str:
        return self._error

    @staticmethod
    def get_devices() -> dict:
        devs = {}
        try:
            devices = pylon.TlFactory.GetInstance().EnumerateDevices()
            for dev in devices:
                serial = dev.GetSerialNumber()
                label = f"{dev.GetModelName()} ({serial})"
                devs[serial] = label
        except Exception as e:
            print(f"Error enumerating Basler devices: {e}")
        return devs

    def create_device(self):
        try:
            device_infos = pylon.TlFactory.GetInstance().EnumerateDevices()
        except Exception as e:
            self._error = str(e)
            self._cap = None
            self._model_name = ""
            return

        if not device_infos:
            self._error = ERR_NOT_FOUND_DEVICE
            self._cap = None
            self._model_name = ""
            return

        if self._config is None:
            self._error = ERR_CONFIG_IS_NONE
            self._cap = None
            self._model_name = ""
            return

        serial = str(self._config.get("id", "") or "").strip()
        dev_info = None
        if serial:
            for info in device_infos:
                if info.GetSerialNumber() == serial:
                    dev_info = info
                    break

        if dev_info is None:
            dev_info = device_infos[0]

        try:
            self._cap = pylon.InstantCamera(
                pylon.TlFactory.GetInstance().CreateDevice(dev_info)
            )
            self._model_name = self._cap.GetDeviceInfo().GetModelName()
            self._error = NO_ERROR
        except Exception as e:
            self._error = str(e)
            self._cap = None

    def _set_enum(self, node_map, name: str, value: str) -> bool:
        try:
            node = genicam.CEnumerationPtr(node_map.GetNode(name))
            if genicam.IsAvailable(node) and genicam.IsWritable(node):
                node.SetValue(value)
                return True
        except Exception:
            pass
        return False

    def _configure_continuous_acquisition(self):
        """Force free-run continuous grabbing (overrides trigger / single-frame modes)."""
        try:
            node_map = self._cap.GetNodeMap()

            # Prefer high-level InstantCamera accessors when available.
            for attr, value in (
                ("AcquisitionMode", "Continuous"),
                ("TriggerMode", "Off"),
            ):
                try:
                    getattr(self._cap, attr).SetValue(value)
                except Exception:
                    pass

            self._set_enum(node_map, "AcquisitionMode", "Continuous")

            trigger_selectors = ("FrameStart", "AcquisitionStart", "LineStart")
            selector_node = genicam.CEnumerationPtr(node_map.GetNode("TriggerSelector"))
            if genicam.IsAvailable(selector_node):
                for selector in trigger_selectors:
                    try:
                        if genicam.IsWritable(selector_node):
                            selector_node.SetValue(selector)
                        self._set_enum(node_map, "TriggerMode", "Off")
                    except Exception:
                        continue
            else:
                self._set_enum(node_map, "TriggerMode", "Off")

            # Disable frame rate limiter if present (can block streaming on some models).
            self._set_enum(node_map, "AcquisitionFrameRateEnable", "0")

            trigger_source = genicam.CEnumerationPtr(node_map.GetNode("TriggerSource"))
            if genicam.IsAvailable(trigger_source) and genicam.IsWritable(trigger_source):
                for source in ("Line1", "Software", "Freerun"):
                    try:
                        trigger_source.SetValue(source)
                        break
                    except Exception:
                        continue
        except Exception as ex:
            print(f"Warning: could not set Basler continuous mode: {ex}")

    def _load_basler_features(self, feature_path: str) -> bool:
        """Load Basler .pfs feature sets only. HIK .ini files must not be applied."""
        if not feature_path:
            return True

        if not os.path.isfile(feature_path):
            print(f"Warning: Basler feature file not found: {feature_path}")
            return False

        ext = os.path.splitext(feature_path)[1].lower()
        if ext != ".pfs":
            print(
                f"Warning: Skipping '{feature_path}' for Basler camera "
                f"(expected .pfs, not {ext or 'unknown'}). "
                "HIK/Webcam feature files must not be loaded on Basler."
            )
            return False

        try:
            pylon.FeaturePersistence.Load(feature_path, self._cap.GetNodeMap(), True)
            return True
        except Exception as ex:
            self._error = ERR_LOAD_FEATURE_FAIL
            print(f"Warning: failed to load Basler feature file: {ex}")
            return False

    def _ensure_converter(self):
        if self._converter is None:
            self._converter = pylon.ImageFormatConverter()
            self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

    def open(self) -> bool:
        if self._cap is None:
            return False

        self._error = NO_ERROR
        try:
            if not self._cap.IsOpen():
                self._cap.Open()
            if not self._cap.IsOpen():
                self._error = ERR_CREATE_DEVICE_FAIL
                return False

            feature = self._config.get("feature", None)
            if feature:
                self._load_basler_features(feature)

            # Always apply after feature load so trigger/acquisition stay in free-run mode.
            self._configure_continuous_acquisition()
            self._ensure_converter()
            return True
        except Exception as ex:
            self._error = str(ex)
            return False

    def close(self) -> bool:
        try:
            if self._cap is not None and self._cap.IsOpen():
                self._cap.Close()
            return True
        except Exception:
            return False

    def start_grabbing(self) -> bool:
        try:
            if not self._cap.IsGrabbing():
                self._cap.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            if self._cap.IsGrabbing():
                self._prime_stream()
            return self._cap.IsGrabbing()
        except Exception as ex:
            self._error = str(ex)
            return False

    def _prime_stream(self, frames: int = 3):
        """Discard initial frames so live feed starts immediately."""
        for _ in range(frames):
            grab_result = self._cap.RetrieveResult(500, pylon.TimeoutHandling_Return)
            if grab_result is not None and grab_result.IsValid():
                grab_result.Release()

    def stop_grabbing(self) -> bool:
        try:
            if self._cap.IsGrabbing():
                self._cap.StopGrabbing()
            return True
        except Exception:
            return False

    def grab(self):
        _mat = None
        grab_result = None
        try:
            if not self._cap.IsGrabbing():
                if not self.start_grabbing():
                    return self._error or ERR_GRAB_FAIL, None

            grab_result = self._cap.RetrieveResult(
                2000, pylon.TimeoutHandling_Return
            )
            if grab_result is None or not grab_result.IsValid():
                self._error = ERR_GRAB_FAIL
                return self._error, None

            if grab_result.GrabSucceeded():
                self._ensure_converter()
                image = self._converter.Convert(grab_result)
                _mat = image.GetArray()
                if _mat is not None and _mat.ndim == 2:
                    _mat = cv2.cvtColor(_mat, cv2.COLOR_GRAY2BGR)
                self._error = NO_ERROR
            else:
                self._error = ERR_GRAB_FAIL
        except Exception as e:
            self._error = str(e)
        finally:
            if grab_result is not None and grab_result.IsValid():
                grab_result.Release()

        return self._error, _mat
