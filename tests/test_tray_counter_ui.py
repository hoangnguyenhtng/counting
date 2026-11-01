import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from tray_counter.app.main_window import MainWindow
from tray_counter.core.camera import SimCamera
from tray_counter.core.order_manager import OrderManager
from tray_counter.core.plc import SimPLC
from tray_counter.core.registry import Registry
from tray_counter.core.storage import Storage
from tray_counter.core.worker import Worker


def test_operator_window_starts_without_models_and_stops_cleanly(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "registry.json"
    path.write_text("{}", encoding="utf-8")
    storage = Storage(tmp_path / "data")
    orders = OrderManager(Registry(path, tmp_path), storage)
    worker = Worker(SimPLC(), SimCamera(tmp_path), None, orders)
    window = MainWindow(worker, lambda kind, source: SimCamera(source))
    window.show()
    until = time.monotonic() + 0.2
    while time.monotonic() < until:
        app.processEvents()
        time.sleep(0.005)
    assert not window.select_button.isEnabled()
    assert window.load_button.isEnabled()
    window.trigger.click()
    until = time.monotonic() + 0.2
    while time.monotonic() < until:
        app.processEvents()
        time.sleep(0.005)
    assert worker.plc.snapshot()["RESULT"] == 3
    window.trigger.click()
    app.processEvents()
    assert window.close()
    assert not worker._thread.is_alive()
