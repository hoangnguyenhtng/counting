from pathlib import Path

import cv2
from PyQt5 import uic
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog, QHeaderView, QInputDialog, QLineEdit, QMainWindow, QMessageBox,
    QTableWidgetItem,
)

from app.paths import get_abs_path
from ..core.models import Status
from ..core.plc import SimPLC
from ..core.storage import annotate


UI_FILE = Path(get_abs_path("tray_counter/app/main_window.ui"))


class Bridge(QObject):
    event = pyqtSignal(str, object)


class MainWindow(QMainWindow):
    def __init__(self, worker, camera_factory, initial_camera_type="SimCamera"):
        super().__init__()
        self.worker, self.camera_factory = worker, camera_factory
        self.state_data, self.preview = {}, None
        # The Designer form is the source of layout, text and widget properties.
        # Keep business logic and signal bindings here so editing the .ui is enough.
        uic.loadUi(str(UI_FILE), self)
        self.bridge = Bridge(self)
        self.bridge.event.connect(self._event)
        self.worker.on_event = self.bridge.event.emit
        self.load_button.clicked.connect(self._load)
        self.export_button.clicked.connect(self._export)
        self.camera_kind.setCurrentText(initial_camera_type)
        self.camera_button.clicked.connect(self._camera)
        self.filter.textChanged.connect(self._filter)
        self.codes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.codes.itemSelectionChanged.connect(self._buttons)
        self.select_button.clicked.connect(self._select)
        self.trays.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.recount_button.clicked.connect(lambda: self.worker.submit("recount", actor=self.actor.text()))
        self.finish_button.clicked.connect(lambda: self.worker.submit("finish", actor=self.actor.text()))
        self.skip_button.clicked.connect(self._skip)
        self.trigger.setEnabled(isinstance(worker.plc, SimPLC))
        self.trigger.toggled.connect(self._trigger)
        self.log.document().setMaximumBlockCount(200)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_plc)
        self.timer.start(100)
        self._buttons()
        self.worker.start()

    def _event(self, kind, value):
        if kind == "state":
            previous = self.state_data.get("current")
            self.state_data = value
            current = value["current"]
            if current and (not previous or current["code"] != previous["code"]):
                self.preview = None
                self.image.clear()
                self.image.setText("Chờ ảnh tray của code " + current["code"])
            selected = self._selected_code()
            self.codes.setRowCount(len(value["orders"]))
            for row, data in enumerate(value["orders"]):
                for col, key in enumerate(("code", "qty_expected", "qty_counted", "status", "model_error")):
                    self.codes.setItem(row, col, QTableWidgetItem(str(data[key]) if data[key] != "" else "Sẵn có"))
                if data["code"] == selected:
                    self.codes.selectRow(row)
            self._filter()
            self.trays.setRowCount(len(value["trays"]))
            for row, tray in enumerate(value["trays"]):
                cells = (tray["seq"], tray["count"], tray["n_foreign"], tray["n_lowconf"],
                         {1: "OK", 2: "NG", 3: "Lỗi"}[tray["result"]],
                         f"Thay bởi #{tray['replaced_by']}" if tray["replaced_by"] else "Hiệu lực")
                for col, cell in enumerate(cells):
                    self.trays.setItem(row, col, QTableWidgetItem(str(cell)))
            if current:
                diff = current["qty_counted"] - current["qty_expected"]
                quantity = "đủ số lượng" if diff == 0 else f"{'thừa' if diff > 0 else 'thiếu'} {abs(diff)}"
                self.summary.setText(f"{current['code']} • {current['qty_counted']} / {current['qty_expected']}\n"
                                     f"{current['status']} • {quantity} • "
                                     f"Lạ: {current['n_foreign']} • Lowconf: {current['n_lowconf']} • Lỗi: {current['n_errors']}")
            label = "Đang nạp model / warm-up" if value["loading"] else "Đang xử lý" if value["working"] else "Sẵn sàng nhận TRIGGER" if value["ready"] else "Chưa sẵn sàng — chọn code hoặc xử lý cảnh báo"
            if value["stalled"]:
                label = "Tác vụ quá thời gian; chờ thiết bị/model phản hồi, sau đó nạp lại code"
            if value["recount_id"]:
                label += " • TRIGGER tiếp theo sẽ THAY kết quả tray vừa rồi"
            self.readiness.setText(label)
            self._buttons()
        elif kind == "tray":
            rgb = cv2.cvtColor(annotate(value["image"], value["result"]), cv2.COLOR_BGR2RGB)
            self.preview = QPixmap.fromImage(QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format_RGB888).copy())
            self._draw_image()
        elif kind in ("log", "error"):
            self.log.append(str(value))
            self.statusBar().showMessage(str(value), 15000)
        elif kind == "confirmation":
            if QMessageBox.question(self, "Xác nhận số lượng lệch", value["message"], QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self.worker.submit("finish", actor=value["actor"], confirm_mismatch=True)
        elif kind == "report":
            self.log.append(f"Đã xuất báo cáo: {value}")
            self.statusBar().showMessage(f"Đã xuất: {value}", 20000)

    def _selected_code(self):
        row = self.codes.currentRow()
        item = self.codes.item(row, 0) if row >= 0 else None
        return item.text() if item else None

    def _filter(self):
        needle = self.filter.text().strip().casefold()
        for row in range(self.codes.rowCount()):
            item = self.codes.item(row, 0)
            self.codes.setRowHidden(row, bool(item and needle not in item.text().casefold()))

    def _buttons(self):
        s = self.state_data
        current = s.get("current") or {}
        idle = not s.get("working") and not s.get("stalled") and not self.trigger.isChecked()
        selected = self._selected_code()
        row = next((r for r in s.get("orders", []) if r["code"] == selected), {})
        self.select_button.setEnabled(bool(idle and selected and not row.get("model_error") and row.get("status") not in (Status.OK.value, Status.SKIPPED.value)))
        self.load_button.setEnabled(idle)
        self.camera_button.setEnabled(idle)
        self.export_button.setEnabled(idle)
        self.recount_button.setEnabled(bool(idle and s.get("trays") and current.get("status") in (Status.COUNTING.value, Status.MISMATCH.value)))
        self.finish_button.setEnabled(bool(idle and current.get("status") == Status.COUNTING.value and not s.get("recount_id")))
        self.skip_button.setEnabled(bool(idle and current.get("status") == Status.MISMATCH.value))

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn Excel sản xuất", "", "Excel (*.xlsx)")
        if path:
            self.worker.submit("load", path=path, actor=self.actor.text())

    def _select(self):
        code = self._selected_code()
        if code:
            self.worker.submit("select", code=code, actor=self.actor.text())

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Xuất báo cáo cuối ca", f"tray_{self.worker.orders.work_date}.xlsx", "Excel (*.xlsx)")
        if path:
            self.worker.submit("export", path=path)

    def _camera(self):
        kind, source = self.camera_kind.currentText(), self.camera_source.text().strip()
        if kind == "SimCamera" and not source:
            source = QFileDialog.getExistingDirectory(self, "Chọn thư mục ảnh tray gốc")
            if not source:
                return
            self.camera_source.setText(source)
        self.worker.submit("camera", factory=lambda: self.camera_factory(kind, source))

    def _skip(self):
        password, ok = QInputDialog.getText(self, "Quản lý", "Mật khẩu quản lý:", QLineEdit.Password)
        if not ok:
            return
        reason, ok = QInputDialog.getText(self, "Bỏ qua code lệch", "Lý do:")
        if ok:
            self.worker.submit("skip", actor=self.actor.text(), password=password, reason=reason)

    def _trigger(self, high):
        if isinstance(self.worker.plc, SimPLC):
            self.worker.plc.set_trigger(high)
            self.trigger.setText("SimPLC: hạ TRIGGER ↓" if high else "SimPLC: bật TRIGGER ↑")
            self._buttons()

    def _refresh_plc(self):
        if isinstance(self.worker.plc, SimPLC):
            values = self.worker.plc.snapshot()
            self.plc_signals.setText("  ".join(f"{key}={value}" for key, value in values.items()))
            if values["TRIGGER"] and values["DONE"]:
                self.readiness.setText("Đã trả kết quả; chờ PLC hạ TRIGGER")

    def _draw_image(self):
        if self.preview:
            self.image.setPixmap(self.preview.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            self._draw_image()

    def closeEvent(self, event):
        try:
            self.worker.stop()
            self.worker.storage.close()
        except Exception as exc:
            QMessageBox.warning(self, "Chưa đóng được thiết bị", str(exc))
            event.ignore()
            return
        self.timer.stop()
        event.accept()
