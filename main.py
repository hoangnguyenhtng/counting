"""Vision Protect Vinyl Attach - Smart Factory G entry point."""
import os
import sys
from pathlib import Path

from app.metadata import APP_DISPLAY_NAME, APP_VERSION, ORGANIZATION_NAME

APP_NAME = APP_DISPLAY_NAME
APP_USER_MODEL_ID = f"SmartFactoryG.VisionProtectVinylAttach.{APP_VERSION}"
_DLL_DIRECTORY_HANDLES = []


def prepare_runtime_environment():
    """Use paths beside the executable so the offline package is portable."""
    if getattr(sys, "frozen", False):
        application_dir = Path(sys.executable).resolve().parent
    else:
        application_dir = Path(__file__).resolve().parent
    os.chdir(application_dir)

    # The offline bundle carries the HIK MVS user-mode runtime. The camera
    # driver itself must still be installed once on the target computer.
    mvs_runtime = application_dir / "MVS_Runtime"
    mvs_control = mvs_runtime / "MvCameraControl.dll"
    if mvs_control.is_file():
        os.environ.setdefault("MVS_DLL_PATH", str(mvs_control))
        if hasattr(os, "add_dll_directory"):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(mvs_runtime)))


prepare_runtime_environment()


def configure_windows_app_identity():
    """Prevent Windows from grouping the app under python.exe's taskbar icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        # The Qt window icon still works on Windows editions without this API.
        pass


configure_windows_app_identity()

# Never attempt package installation or downloads on the factory computer.
os.environ.setdefault("YOLO_AUTOINSTALL", "false")

# PyQt5 bundles an older Microsoft C++ runtime.  On Windows, importing Qt
# first can make PyTorch fail while loading c10.dll (WinError 1114), so load
# PyTorch before any PyQt5 module.
import torch


def allocate_console():
    """Open a diagnostics console only when explicitly requested."""
    if sys.platform == "win32" and os.environ.get("VPVA_DEBUG_CONSOLE") == "1":
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:
            ctypes.windll.kernel32.AllocConsole()
            sys.stdout = open("CONOUT$", "w", encoding="utf-8")
            sys.stderr = open("CONOUT$", "w", encoding="utf-8")
            print("--- Console window allocated for logging ---")


allocate_console()

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication
from app.views.main_window import MainWindow


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORGANIZATION_NAME)
    icon_path = Path("res/App/smart_factory_g.ico").resolve()
    app_icon = QIcon(str(icon_path))
    app.setWindowIcon(app_icon)
    app.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Enable console printing of EventBus log messages
    from app.signals import bus
    from datetime import datetime

    def print_console_log(message: str, level: str = "INF"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

    bus.log_message.connect(print_console_log)

    # Load and apply QSS styles
    try:
        with open("res/Style/style.qss", "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except FileNotFoundError:
        print("Style file style.qss not found, using default styling.")
    except Exception as e:
        print(f"Error loading stylesheet: {e}")

    window = MainWindow()
    window.setWindowIcon(app_icon)
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
