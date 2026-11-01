import os
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from app.metadata import APP_DISPLAY_NAME
from tray_counter.app import main_window
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
    assert window.windowTitle() == APP_DISPLAY_NAME
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


def test_designer_edits_are_used_directly_and_keep_button_bindings(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    form = ET.parse(main_window.UI_FILE)
    title = form.find("./widget/property[@name='windowTitle']/string")
    title.text = "Tray Counter • Line 2"
    form.find(".//widget[@name='load_button']/property[@name='text']/string").text = "Chọn lệnh sản xuất"
    form.find(".//widget[@name='actor']/property[@name='placeholderText']/string").text = "Mã người vận hành"
    form.find(".//layout[@name='main_layout']/property[@name='spacing']/number").text = "17"
    edited = tmp_path / "edited.ui"
    form.write(edited, encoding="utf-8", xml_declaration=True)
    monkeypatch.setattr(main_window, "UI_FILE", edited)
    worker = SimpleNamespace(plc=SimPLC(), storage=SimpleNamespace(close=Mock()), start=Mock(), stop=Mock(), submit=Mock())
    window = MainWindow(worker, Mock())
    try:
        assert window.windowTitle() == "Tray Counter • Line 2"
        assert window.load_button.text() == "Chọn lệnh sản xuất"
        assert window.actor.placeholderText() == "Mã người vận hành"
        assert window.centralWidget().layout().spacing() == 17
        window.actor.setText("NV002")
        monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", lambda *args: ("orders.xlsx", "Excel (*.xlsx)"))
        window.load_button.click()
        worker.submit.assert_called_once_with("load", path="orders.xlsx", actor="NV002")
        window.trigger.click()
        assert worker.plc.read_trigger()
        window.trigger.click()
        assert not worker.plc.read_trigger()
    finally:
        window.close()
        app.processEvents()
