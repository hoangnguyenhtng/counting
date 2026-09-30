import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt5.QtGui import QMouseEvent, QPixmap
from PyQt5.QtWidgets import QApplication, QComboBox, QTableWidget, QTableWidgetItem, QTextEdit

from app.views.canvas_widget import Canvas
from app.views.main_window import MainWindow, SYSTEM_LOG_LIMIT, UI_LOG_LIMIT
from app.views.shape import Shape


class CanvasInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.canvas = Canvas()
        self.canvas.resize(640, 480)
        self.canvas.load_pixmap(QPixmap(640, 480), fit=False)

    @staticmethod
    def _move_event(x, y):
        return QMouseEvent(
            QEvent.MouseMove,
            QPointF(x, y),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

    def test_roi_is_normalized_when_drawn_up_and_left(self):
        self.canvas.drawing = True
        self.canvas.start_pos = QPointF(300, 300)

        self.canvas.mouseMoveEvent(self._move_event(100, 120))

        self.assertEqual(self.canvas.current, QRectF(100, 120, 200, 180))
        self.assertGreater(self.canvas.current.width(), 0)
        self.assertGreater(self.canvas.current.height(), 0)

    def test_unlocked_roi_moves_during_mouse_drag(self):
        shape = Shape("ROI")
        for point in ((100, 100), (200, 100), (200, 200), (100, 200)):
            shape.addPoint(*point)
        shape.lock = False
        shape.selected = True
        self.canvas.shapes = [shape]
        self.canvas.idSelected = 0
        self.canvas.moving = True
        self.canvas.start_pos = QPointF(100, 100)

        self.canvas.mouseMoveEvent(self._move_event(125, 130))

        self.assertEqual(shape.points[0], QPointF(125, 130))

    def test_frozen_teaching_view_discards_queued_live_frame(self):
        window = Mock()
        window.is_auto_running = False
        window._static_image_mode = True
        window.current_frame = None
        window.canvas = Mock()
        worker = Mock()

        MainWindow._on_camera_frame(
            window,
            np.zeros((8, 8, 3), dtype=np.uint8),
            worker,
        )

        window._display_frame.assert_not_called()
        worker.mark_frame_consumed.assert_called_once_with()

    def test_ui_log_table_keeps_only_the_newest_100_rows(self):
        table = QTableWidget(0, 1)
        for index in range(UI_LOG_LIMIT):
            table.insertRow(index)
            table.setItem(index, 0, QTableWidgetItem(str(index)))

        MainWindow._make_room_for_log_row(table)
        table.insertRow(table.rowCount())
        table.setItem(table.rowCount() - 1, 0, QTableWidgetItem("newest"))

        self.assertEqual(table.rowCount(), UI_LOG_LIMIT)
        self.assertEqual(table.item(0, 0).text(), "1")
        self.assertEqual(table.item(UI_LOG_LIMIT - 1, 0).text(), "newest")

    def test_system_log_keeps_first_200_lines_and_discards_newest_overflow(self):
        log_widget = QTextEdit()
        for index in range(SYSTEM_LOG_LIMIT):
            self.assertTrue(
                MainWindow._append_system_log_line(log_widget, f"line-{index}")
            )

        self.assertFalse(
            MainWindow._append_system_log_line(log_widget, "discard-this-newest-line")
        )
        lines = log_widget.toPlainText().splitlines()
        self.assertEqual(len(lines), SYSTEM_LOG_LIMIT)
        self.assertEqual(lines[0], "line-0")
        self.assertEqual(lines[-1], f"line-{SYSTEM_LOG_LIMIT - 1}")

    def test_plc_com_list_keeps_manually_entered_port_when_refreshing(self):
        window = Mock()
        window.combo_light_com = QComboBox()
        window.combo_light_com.setEditable(True)
        window.combo_light_com.addItem("COM9")
        window.combo_light_com.setCurrentText("COM9")
        window.combo_plc_com = QComboBox()
        window.combo_plc_com.addItem("COM8")
        window.combo_plc_com.setCurrentText("COM8")
        detected = [SimpleNamespace(device="COM3"), SimpleNamespace(device="COM5")]

        with patch("app.views.main_window.serial.tools.list_ports.comports", return_value=detected):
            MainWindow._refresh_com_ports(window)

        self.assertEqual(
            [window.combo_plc_com.itemText(index) for index in range(window.combo_plc_com.count())],
            ["COM3", "COM5", "COM8"],
        )
        self.assertEqual(window.combo_plc_com.currentText(), "COM8")
        self.assertEqual(window.combo_light_com.currentText(), "COM9")


if __name__ == "__main__":
    unittest.main()
