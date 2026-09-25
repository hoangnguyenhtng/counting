from hardware.cameras.base_camera import BaseCamera
from hardware.cameras.hik import HIK
from hardware.cameras.webcam import Webcam

BASLER_ALIASES = frozenset({"SODA", "Basler", "BASLER"})


def is_basler_camera_type(camera_type: str) -> bool:
    return (camera_type or "").strip() in BASLER_ALIASES


def _get_soda_class():
    """Load the optional Basler driver only when it is actually needed."""
    from hardware.cameras.soda import SODA

    return SODA


def get_camera_devices(camera_type: str = "") -> dict:
    """Enumerate cameras for the selected driver, merging Basler when requested."""
    camera_type = (camera_type or "").strip()

    if camera_type == "HIK":
        try:
            return HIK.get_devices()
        except (OSError, FileNotFoundError, ImportError, AttributeError) as exc:
            print(f"Warning: Hikvision MVS unavailable ({exc}).")
            return {}

    if is_basler_camera_type(camera_type):
        try:
            SODA = _get_soda_class()
            return SODA.get_devices()
        except Exception as exc:
            print(f"Warning: Basler pypylon unavailable ({exc}).")
            return {}

    if camera_type == "Webcam":
        return Webcam.get_devices()

    devices = {}
    try:
        devices.update(HIK.get_devices())
    except (OSError, FileNotFoundError, ImportError, AttributeError) as exc:
        print(f"Warning: Hikvision MVS unavailable ({exc}).")

    try:
        SODA = _get_soda_class()
        devices.update(SODA.get_devices())
    except Exception as exc:
        print(f"Warning: Basler pypylon unavailable ({exc}).")

    if not devices:
        devices = Webcam.get_devices()
    return devices
