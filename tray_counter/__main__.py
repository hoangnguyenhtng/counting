import argparse
import sys
from pathlib import Path

import yaml

from app.metadata import APP_DISPLAY_NAME, APP_VERSION, ORGANIZATION_NAME
from app.paths import get_abs_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"{APP_DISPLAY_NAME} — đếm tray theo code và PLC trigger")
    package_root = Path(sys.executable).resolve().parent / "tray_counter" if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    parser.add_argument("--settings", default=str(package_root / "config" / "settings.yaml"))
    args = parser.parse_args(argv)
    path = Path(args.settings).resolve()
    root = path.parent.parent

    # The counting application uses ONNX Runtime and does not initialize Torch.
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from .app.main_window import MainWindow
    from .core.camera import IndustrialCamera, SimCamera
    from .core.order_manager import OrderManager
    from .core.plc import SimPLC
    from .core.recorder import Recorder
    from .core.registry import Registry
    from .core.storage import Storage
    from .core.verifier import RegistryVerifier
    from .core.worker import Worker

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication([sys.argv[0]])
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setWindowIcon(QIcon(get_abs_path("res/App/smart_factory_g.ico")))
    storage = camera = worker = None
    try:
        settings = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if not isinstance(settings, dict):
            raise ValueError("settings.yaml phải là cấu hình dạng ánh xạ")
        if settings.get("plc", {}).get("type", "sim") != "sim":
            raise ValueError("Chưa cấu hình adapter PLC thật; cần chốt giao thức và địa chỉ thanh ghi")
        registry = Registry(root / settings["registry"], root)
        storage = Storage(root / settings.get("data_dir", "data"))
        storage.cleanup_images(settings.get("images", {}).get("keep_days", 30))
        orders = OrderManager(registry, storage, **settings.get("excel", {}))
        camera_cfg = settings.get("camera", {})
        notices = []

        def camera_factory(kind, source):
            if kind == "SimCamera":
                return SimCamera(root / source, camera_cfg.get("loop", False))
            return IndustrialCamera(kind, source, str(root / camera_cfg["feature"]) if camera_cfg.get("feature") else "",
                                    camera_cfg.get("exposure_us", 10000), camera_cfg.get("gain", 0), camera_cfg.get("timeout_s", 2),
                                    on_error=lambda message: notices.append(("camera", message)))

        kind = camera_cfg.get("type", "SimCamera")
        camera = camera_factory(kind, camera_cfg.get("folder", "sim_images") if kind == "SimCamera" else camera_cfg.get("id", ""))
        verifier = RegistryVerifier(registry, settings.get("onnx", {}).get("providers"))
        video = settings.get("video", {})
        recorder = Recorder(camera, root / video.get("folder", "data/video"), video.get("fps", 10), video.get("size"),
                            on_error=lambda message: notices.append(("recorder", message))) if video.get("enabled", False) else None
        worker = Worker(SimPLC(), camera, verifier, orders, recorder=recorder,
                        heartbeat_s=settings.get("plc", {}).get("heartbeat_s", 0.5),
                        poll_ms=settings.get("plc", {}).get("poll_ms", 10), **settings.get("worker", {}))
        window = MainWindow(worker, camera_factory, kind)
        from PyQt5.QtCore import QTimer
        notice_timer = QTimer(window)

        def drain_notices():
            while notices:
                source, message = notices.pop(0)
                try:
                    storage.event(source, message, level="ERROR")
                except Exception as exc:
                    # A failed optional recorder/camera log must not escape a Qt slot.
                    window.log.append(f"Không ghi được event: {exc}")
                window.log.append(f"{source}: {message}")

        notice_timer.timeout.connect(drain_notices)
        notice_timer.start(250)
        window.show()
        return app.exec_()
    except Exception as exc:
        QMessageBox.critical(None, "Lỗi cấu hình đếm tray", str(exc))
        if worker:
            worker.stop()
        elif camera:
            camera.close()
        if storage:
            storage.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
