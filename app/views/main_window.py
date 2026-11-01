"""
Tray Counter — Legacy tape inspection window
Two-tab UI: Teaching & Setup | Auto Inspection
"""
import os
import sys

# Keep PyTorch ahead of PyQt5's bundled C++ runtime on Windows.  Importing
# Qt first can cause torch's c10.dll initialization to fail (WinError 1114).
import torch

import cv2
import numpy as np
from datetime import datetime
from functools import partial

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QTabWidget, QGroupBox, QPushButton, QComboBox,
    QLabel, QTableWidget, QTableWidgetItem, QFileDialog,
    QMessageBox, QSplitter, QFrame, QHeaderView,
    QSpinBox, QDoubleSpinBox, QLineEdit, QCheckBox, QTextEdit,
    QSizePolicy, QAbstractSpinBox, QScrollArea, QInputDialog, QShortcut,
    QProgressBar,
)
from PyQt5.QtCore import QEventLoop, QThread, Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QIcon, QImage, QPixmap, QColor, QIntValidator, QKeySequence

from app.signals import bus
from app.paths import get_abs_path
from app.metadata import APP_DISPLAY_NAME, APP_VERSION, ORGANIZATION_NAME, PROJECT_NAME
from app.security import teaching_password_is_valid
from app.models.config import AppConfig, ImageRetentionConfig
from app.models.result import DetectionResult
from app.controllers.auto_controller import AutoController
from app.core.image_storage import save_captured_image
from app.core.image_retention import ImageRetentionService, RetentionPolicy
from app.core.folder_crop import CropRegion, crop_folder, list_images, read_image
from app.controllers.config_controller import ConfigController
from app.controllers.hardware_controller import HardwareController
from app.core.model_pool import ModelPool
from app.core.detection import detect_tape

from hardware.cameras import get_camera_devices

from app.views.canvas_widget import Canvas
from app.views.shape import Shape as MyShape

from data.json_handler import HandleJsonPBA
from data.database import DatabaseManager
from data.logger import Logger

from app.workers.camera_worker import CameraWorker
from app.workers.auto_worker import AutoWorker
from app.workers.developer_worker import DeveloperWorker

import serial.tools.list_ports


INSPECTION_LOG_LIMIT = 100
SYSTEM_LOG_LIMIT = 200
# Backward-compatible name used by existing tests and extensions.
UI_LOG_LIMIT = INSPECTION_LOG_LIMIT


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("MainWindow")
        self.setWindowTitle(f"{APP_DISPLAY_NAME} V{APP_VERSION}")
        self.setWindowIcon(QIcon(get_abs_path("res/App/smart_factory_g.ico")))
        self.setMinimumSize(760, 520)

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self._compact_ui = available.width() < 1280 or available.height() < 760
            self.resize(
                min(1180, max(760, int(available.width() * 0.92))),
                min(760, max(520, int(available.height() * 0.90))),
            )
        else:
            self._compact_ui = False
        self.setProperty("compact", self._compact_ui)

        # 1. Initialize data managers & controllers
        self.json_handler = HandleJsonPBA()
        self.config_controller = ConfigController(self.json_handler)
        self.hardware_controller = HardwareController()

        # Core ML Models & Pipeline
        self.model_pool = ModelPool()

        # Auto Inspection Loop
        self.auto_controller = AutoController(
            self.hardware_controller,
            self.model_pool,
        )

        # Main Logger
        self.main_logger = Logger("Main")
        default_retention = ImageRetentionConfig()
        self.image_retention = ImageRetentionService(
            get_abs_path("Log_Vision/LogImage"),
            RetentionPolicy.from_config(default_retention),
            log_callback=lambda message, level: bus.log_message.emit(message, level),
        )
        self.main_logger.set_image_retention(self.image_retention)

        # Database Manager
        self.db_manager = DatabaseManager("database.db")
        from sqlalchemy import String, Float, DateTime
        self.db_table = self.db_manager.create_table(
            "inspections",
            model_name=String,
            result=String,
            label_counts=String,
            timecheck=String,
        )

        # State
        self.current_config: AppConfig = None
        self.current_frame = None
        self.camera_worker: CameraWorker = None
        self.auto_worker: AutoWorker = None
        self.developer_worker: DeveloperWorker = None
        self.is_auto_running = False
        self.is_developer_running = False
        self.is_developer_armed = False
        self._developer_folder = None
        self._developer_images = []
        self._developer_config = None
        self._developer_opened_tcp = False
        self._developer_restart_pending = False
        self._developer_loop_count = 0
        self._static_image_mode = False
        self._crop_source_folder = None
        self._auto_opened_io = {"tcp": False, "com": False}
        self._auto_opened_camera = False
        self._auto_opened_light = False
        self._teaching_unlocked = False
        self._last_authorized_tab_index = 0
        self._handling_protected_tab_change = False

        # Counters
        self.count_ok = 0
        self.count_ng = 0
        self.count_total = 0

        # 2. Build UI
        self._build_ui()

        # 3. Connect signals
        self._connect_signals()

        # Standard application shortcut for saving the active model settings.
        self.shortcut_save_config = QShortcut(QKeySequence.Save, self)
        self.shortcut_save_config.setContext(Qt.WindowShortcut)
        self.shortcut_save_config.activated.connect(self._on_save_shortcut)

        # 4. Load default config
        self._load_initial_config()
        self.image_retention.start()

    # ================================================================
    # BUILD UI
    # ================================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(5)

        # ── Project identity and Header Bar ──
        main_layout.addWidget(self._build_project_banner())
        header = self._build_header()
        main_layout.addWidget(header)

        # ── Tab Widget ──
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("main_tabs")
        self.tab_widget.tabBar().setUsesScrollButtons(True)
        self.tab_widget.tabBar().setExpanding(False)

        # Build the pages first, then add them in the operator workflow order.
        self.tab_teaching = self._build_teaching_tab()
        self.tab_auto = self._build_auto_tab()
        self.tab_developer = self._build_developer_tab()
        self.tab_io = self._build_io_tab()

        self.tab_widget.addTab(self.tab_auto, "Auto")
        self.tab_widget.addTab(self.tab_developer, "Developer")
        self.tab_widget.addTab(self.tab_teaching, "Teaching")
        self.tab_widget.addTab(self.tab_io, "I/O")
        self.tab_widget.setCurrentIndex(0)
        main_layout.addWidget(self.tab_widget, 1)

        # ── Status Bar ──
        status_bar = self.statusBar()
        status_bar.showMessage("Ready")
        self.label_bypass_indicator = QLabel("BYPASS", status_bar)
        self.label_bypass_indicator.setObjectName("bypass_indicator")
        self.label_bypass_indicator.setAlignment(Qt.AlignCenter)
        self.label_bypass_indicator.setFixedSize(78, 20)
        self.label_bypass_indicator.move(6, 2)
        self.label_bypass_indicator.hide()

        self._bypass_blink_timer = QTimer(self)
        self._bypass_blink_timer.setInterval(500)
        self._bypass_blink_timer.timeout.connect(self._blink_bypass_indicator)

    def _build_project_banner(self) -> QWidget:
        banner = QFrame()
        banner.setObjectName("project_banner")
        banner.setMinimumHeight(44)
        banner.setMaximumHeight(52)
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 4, 14, 4)

        project_title = QLabel(PROJECT_NAME.upper())
        project_title.setObjectName("project_title")
        project_title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addWidget(project_title, 1)

        project_version = QLabel(f"{ORGANIZATION_NAME}  •  V{APP_VERSION}")
        project_version.setObjectName("project_version")
        project_version.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        layout.addWidget(project_version)
        return banner

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("app_header")
        header.setMinimumHeight(72)
        header.setMaximumHeight(82)
        layout = QGridLayout(header)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        # Model selector
        layout.addWidget(QLabel("Model:"), 0, 0)
        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(110)
        self.combo_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.combo_model, 0, 1)

        self.btn_load_config = QPushButton("Load")
        self.btn_load_config.clicked.connect(lambda: self._on_load_config(show_popup=True))
        layout.addWidget(self.btn_load_config, 0, 2)

        self.btn_save_config = QPushButton("Save")
        self.btn_save_config.clicked.connect(self._on_save_config)
        layout.addWidget(self.btn_save_config, 0, 3)

        self.btn_new_config = QPushButton("New")
        self.btn_new_config.clicked.connect(self._on_new_config)
        layout.addWidget(self.btn_new_config, 0, 4)

        # Hardware status indicators
        self.hw_indicators = {}
        for name, icon_on, icon_off in [
            ("Camera", "📷", "⬜"), ("TCP", "🌐", "⬜"),
            ("COM", "🔌", "⬜"), ("Light", "💡", "⬜"),
        ]:
            lbl = QLabel(f"{icon_off} {name}")
            lbl.setStyleSheet("padding: 1px 5px;")
            layout.addWidget(lbl, 1, len(self.hw_indicators))
            self.hw_indicators[name] = (lbl, icon_on, icon_off)

        # Auto Start/Stop
        self.btn_start_auto = QPushButton("▶ Start Auto")
        self.btn_start_auto.setObjectName("btn_start_auto")
        self.btn_start_auto.setMinimumWidth(105)
        self.btn_start_auto.clicked.connect(self._on_start_auto)
        layout.addWidget(self.btn_start_auto, 0, 6)

        self.btn_stop_auto = QPushButton("■ Stop Auto")
        self.btn_stop_auto.setObjectName("btn_stop_auto")
        self.btn_stop_auto.setMinimumWidth(105)
        self.btn_stop_auto.setEnabled(False)
        self.btn_stop_auto.clicked.connect(self._on_stop_auto)
        layout.addWidget(self.btn_stop_auto, 0, 7)

        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(5, 1)

        return header

    # ── Teaching Tab ──
    def _build_teaching_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # Left: Canvas
        canvas_container = QWidget()
        canvas_container.setObjectName("canvas_card")
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(5)

        canvas_header = QFrame()
        canvas_header.setObjectName("canvas_header")
        canvas_header_layout = QHBoxLayout(canvas_header)
        canvas_header_layout.setContentsMargins(10, 5, 10, 5)
        canvas_title = QLabel("TEACHING VIEW")
        canvas_title.setObjectName("canvas_title")
        canvas_header_layout.addWidget(canvas_title)
        canvas_header_layout.addStretch()
        canvas_hint = QLabel("Wheel: zoom  •  Drag: pan  •  Enter: fit")
        canvas_hint.setObjectName("canvas_hint")
        canvas_header_layout.addWidget(canvas_hint)
        canvas_layout.addWidget(canvas_header)

        # Teaching workspace: source/ROI tools on the left, scaled image on the right.
        teaching_workspace = QWidget()
        teaching_workspace_layout = QHBoxLayout(teaching_workspace)
        teaching_workspace_layout.setContentsMargins(0, 0, 0, 0)
        teaching_workspace_layout.setSpacing(7)

        teaching_tools = QFrame()
        teaching_tools.setObjectName("teaching_tools")
        teaching_tools.setMinimumWidth(148)
        teaching_tools.setMaximumWidth(174)
        teaching_tools_layout = QVBoxLayout(teaching_tools)
        teaching_tools_layout.setContentsMargins(8, 9, 8, 9)
        teaching_tools_layout.setSpacing(6)

        source_title = QLabel("IMAGE SOURCE")
        source_title.setObjectName("teaching_tool_title")
        teaching_tools_layout.addWidget(source_title)

        self.btn_capture = QPushButton("📸 Capture")
        self.btn_capture.clicked.connect(self._on_capture)
        teaching_tools_layout.addWidget(self.btn_capture)

        self.btn_load_image = QPushButton("📂 Load Image")
        self.btn_load_image.clicked.connect(self._on_load_image)
        teaching_tools_layout.addWidget(self.btn_load_image)

        self.btn_select_crop_folder = QPushButton("Load Folder")
        self.btn_select_crop_folder.setToolTip(
            "Select an image folder and display its first readable image"
        )
        self.btn_select_crop_folder.clicked.connect(self._on_select_crop_folder)
        teaching_tools_layout.addWidget(self.btn_select_crop_folder)

        self.btn_crop_folder = QPushButton("Crop Folder")
        self.btn_crop_folder.setToolTip(
            "Apply all visible ROIs to every image in the selected folder"
        )
        self.btn_crop_folder.clicked.connect(self._on_crop_folder)
        teaching_tools_layout.addWidget(self.btn_crop_folder)

        self.crop_folder_label = QLabel("Folder: not selected")
        self.crop_folder_label.setObjectName("crop_folder_label")
        self.crop_folder_label.setWordWrap(True)
        teaching_tools_layout.addWidget(self.crop_folder_label)

        self.btn_live_camera = QPushButton("LIVE: OFF")
        self.btn_live_camera.setObjectName("btn_live_camera")
        self.btn_live_camera.setCheckable(True)
        self.btn_live_camera.setEnabled(False)
        self.btn_live_camera.clicked.connect(self._on_toggle_live_camera)
        teaching_tools_layout.addWidget(self.btn_live_camera)

        roi_title = QLabel("ROI TOOLS")
        roi_title.setObjectName("teaching_tool_title")
        teaching_tools_layout.addWidget(roi_title)

        self.btn_draw_roi = QPushButton("🔲 Draw ROI")
        self.btn_draw_roi.clicked.connect(self._on_draw_roi)
        teaching_tools_layout.addWidget(self.btn_draw_roi)

        self.btn_clear_roi = QPushButton("🗑 Clear ROI")
        self.btn_clear_roi.clicked.connect(self._on_clear_roi)
        teaching_tools_layout.addWidget(self.btn_clear_roi)

        self.btn_test = QPushButton("🧪 Test Detection")
        self.btn_test.setObjectName("btn_test")
        self.btn_test.clicked.connect(self._on_test_detection)
        teaching_tools_layout.addWidget(self.btn_test)
        teaching_tools_layout.addStretch(1)

        self.canvas = Canvas()
        self.canvas.setMinimumSize(320, 240)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        teaching_workspace_layout.addWidget(teaching_tools)
        teaching_workspace_layout.addWidget(self.canvas, 1)
        canvas_layout.addWidget(teaching_workspace, 1)

        splitter.addWidget(canvas_container)

        # Right: Settings Panel (scrollable)
        right_panel = QWidget()
        right_panel.setObjectName("settings_panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(8)

        # Camera Group
        cam_group = QGroupBox("Camera")
        cam_layout = QGridLayout(cam_group)

        cam_layout.addWidget(QLabel("Type:"), 0, 0)
        self.combo_camera_type = QComboBox()
        self.combo_camera_type.addItems(["Webcam", "HIK", "Basler"])
        self.combo_camera_type.currentTextChanged.connect(self._refresh_camera_devices)
        cam_layout.addWidget(self.combo_camera_type, 0, 1)

        cam_layout.addWidget(QLabel("Device:"), 1, 0)
        self.combo_camera_id = QComboBox()
        cam_layout.addWidget(self.combo_camera_id, 1, 1)

        self.btn_refresh_cam = QPushButton("🔄")
        self.btn_refresh_cam.setFixedWidth(36)
        self.btn_refresh_cam.clicked.connect(self._refresh_camera_devices)
        cam_layout.addWidget(self.btn_refresh_cam, 1, 2)

        cam_layout.addWidget(QLabel("Delay(ms):"), 2, 0)
        self.spin_cam_delay = QSpinBox()
        self.spin_cam_delay.setRange(0, 5000)
        self.spin_cam_delay.setValue(100)
        cam_layout.addWidget(self.spin_cam_delay, 2, 1)

        btn_cam_row = QHBoxLayout()
        self.btn_open_cam = QPushButton("Connect")
        self.btn_open_cam.clicked.connect(self._on_open_camera)
        btn_cam_row.addWidget(self.btn_open_cam)

        self.btn_close_cam = QPushButton("Disconnect")
        self.btn_close_cam.clicked.connect(self._on_close_camera)
        self.btn_close_cam.setEnabled(False)
        btn_cam_row.addWidget(self.btn_close_cam)
        cam_layout.addLayout(btn_cam_row, 3, 0, 1, 3)

        right_layout.addWidget(cam_group)

        # YOLO Model Group
        yolo_group = QGroupBox("YOLO Model")
        yolo_layout = QGridLayout(yolo_group)

        yolo_layout.addWidget(QLabel("Model (.pt):"), 0, 0)
        self.combo_yolo_model = QComboBox()
        yolo_layout.addWidget(self.combo_yolo_model, 0, 1)

        self.btn_refresh_models = QPushButton("🔄")
        self.btn_refresh_models.setFixedWidth(36)
        self.btn_refresh_models.clicked.connect(self._refresh_ai_models)
        yolo_layout.addWidget(self.btn_refresh_models, 0, 2)

        yolo_layout.addWidget(QLabel("Confidence:"), 1, 0)
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setRange(0.01, 1.0)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(0.25)
        yolo_layout.addWidget(self.spin_conf, 1, 1)

        yolo_layout.addWidget(QLabel("IoU:"), 2, 0)
        self.spin_iou = QDoubleSpinBox()
        self.spin_iou.setRange(0.01, 1.0)
        self.spin_iou.setSingleStep(0.05)
        self.spin_iou.setValue(0.45)
        yolo_layout.addWidget(self.spin_iou, 2, 1)

        yolo_layout.addWidget(QLabel("Max Detect:"), 3, 0)
        self.spin_max_det = QSpinBox()
        self.spin_max_det.setRange(1, 10000)
        self.spin_max_det.setValue(100)
        self.spin_max_det.setToolTip(
            "Keep only the detections with the highest confidence scores"
        )
        yolo_layout.addWidget(self.spin_max_det, 3, 1)

        right_layout.addWidget(yolo_group)

        # Light Group
        light_group = QGroupBox("Light Controller")
        light_layout = QGridLayout(light_group)

        light_layout.addWidget(QLabel("Type:"), 0, 0)
        self.combo_light_type = QComboBox()
        self.combo_light_type.addItems(["LCPController", "DCPController"])
        light_layout.addWidget(self.combo_light_type, 0, 1)

        light_layout.addWidget(QLabel("COM:"), 1, 0)
        self.combo_light_com = QComboBox()
        self.combo_light_com.setEditable(True)
        light_layout.addWidget(self.combo_light_com, 1, 1)

        light_layout.addWidget(QLabel("Baud:"), 2, 0)
        self.combo_light_baud = QComboBox()
        self.combo_light_baud.addItems(["9600", "19200", "38400", "115200"])
        light_layout.addWidget(self.combo_light_baud, 2, 1)

        ch_row = QGridLayout()
        for i in range(4):
            row = i // 2
            column = (i % 2) * 2
            ch_row.addWidget(QLabel(f"CH{i}:"), row, column)
            spin = QSpinBox()
            spin.setRange(0, 255)
            spin.setValue(0)
            spin.valueChanged.connect(self._on_light_value_changed)
            setattr(self, f"spin_light_ch{i}", spin)
            ch_row.addWidget(spin, row, column + 1)
        light_layout.addLayout(ch_row, 3, 0, 1, 2)

        light_layout.addWidget(QLabel("Delay(ms):"), 4, 0)
        self.spin_light_delay = QSpinBox()
        self.spin_light_delay.setRange(0, 5000)
        self.spin_light_delay.setValue(200)
        light_layout.addWidget(self.spin_light_delay, 4, 1)

        btn_light_row = QHBoxLayout()
        self.btn_open_light = QPushButton("Connect")
        self.btn_open_light.clicked.connect(self._on_open_light)
        btn_light_row.addWidget(self.btn_open_light)
        self.btn_close_light = QPushButton("Disconnect")
        self.btn_close_light.clicked.connect(self._on_close_light)
        self.btn_close_light.setEnabled(False)
        btn_light_row.addWidget(self.btn_close_light)
        light_layout.addLayout(btn_light_row, 5, 0, 1, 2)

        self.btn_toggle_light = QPushButton("LIGHT: OFF")
        self.btn_toggle_light.setObjectName("btn_toggle_light")
        self.btn_toggle_light.setCheckable(True)
        self.btn_toggle_light.setEnabled(False)
        self.btn_toggle_light.clicked.connect(self._on_toggle_light)
        light_layout.addWidget(self.btn_toggle_light, 6, 0, 1, 2)

        self.light_group = light_group
        right_layout.addWidget(self.light_group)

        # I/O Connection Group
        plc_group = QGroupBox("I/O Connections")
        plc_layout = QGridLayout(plc_group)

        # TCP
        plc_layout.addWidget(QLabel("TCP IP:"), 0, 0)
        self.line_tcp_ip = QLineEdit("127.0.0.1")
        plc_layout.addWidget(self.line_tcp_ip, 0, 1, 1, 2)

        plc_layout.addWidget(QLabel("Port:"), 0, 3)
        self.line_tcp_port = QLineEdit("5000")
        self.line_tcp_port.setValidator(QIntValidator(1, 65535, self.line_tcp_port))
        self.line_tcp_port.setMinimumWidth(52)
        plc_layout.addWidget(self.line_tcp_port, 0, 4)

        self.btn_tcp_start = QPushButton("Start TCP")
        self.btn_tcp_start.clicked.connect(self._on_start_tcp)
        plc_layout.addWidget(self.btn_tcp_start, 1, 0, 1, 2)

        self.btn_tcp_stop = QPushButton("Stop")
        self.btn_tcp_stop.clicked.connect(self._on_stop_tcp)
        self.btn_tcp_stop.setEnabled(False)
        plc_layout.addWidget(self.btn_tcp_stop, 1, 2, 1, 3)

        self.chk_use_tcp = QCheckBox("Use TCP (trigger + result)")
        self.chk_use_tcp.setChecked(True)
        plc_layout.addWidget(self.chk_use_tcp, 2, 0, 1, 5)

        # COM
        plc_layout.addWidget(QLabel("COM:"), 3, 0)
        self.combo_plc_com = QComboBox()
        self.combo_plc_com.setEditable(True)
        self.combo_plc_com.setPlaceholderText("No COM port")
        self.combo_plc_com.setToolTip(
            "Select a detected COM port or enter one manually"
        )
        plc_layout.addWidget(self.combo_plc_com, 3, 1)

        self.btn_refresh_plc_com = QPushButton("Refresh")
        self.btn_refresh_plc_com.setToolTip("Scan available COM ports again")
        self.btn_refresh_plc_com.clicked.connect(self._refresh_com_ports)
        plc_layout.addWidget(self.btn_refresh_plc_com, 3, 2)

        plc_layout.addWidget(QLabel("Baud:"), 3, 3)
        self.combo_plc_baud = QComboBox()
        self.combo_plc_baud.addItems(["9600", "19200", "38400", "115200"])
        plc_layout.addWidget(self.combo_plc_baud, 3, 4)

        self.btn_com_open = QPushButton("Open COM")
        self.btn_com_open.clicked.connect(self._on_open_com)
        plc_layout.addWidget(self.btn_com_open, 4, 0, 1, 2)

        self.btn_com_close = QPushButton("Close")
        self.btn_com_close.clicked.connect(self._on_close_com)
        self.btn_com_close.setEnabled(False)
        plc_layout.addWidget(self.btn_com_close, 4, 2, 1, 3)

        self.chk_use_com = QCheckBox("Use COM (trigger + result)")
        self.chk_use_com.setChecked(False)
        plc_layout.addWidget(self.chk_use_com, 5, 0, 1, 5)

        # Trigger code
        plc_layout.addWidget(QLabel("Trigger code:"), 6, 0)
        self.line_trigger_code = QLineEdit("L1")
        plc_layout.addWidget(self.line_trigger_code, 6, 1, 1, 4)

        self.io_connection_group = plc_group

        teaching_log_group = QGroupBox("Teaching Log")
        teaching_log_layout = QVBoxLayout(teaching_log_group)
        self.text_teaching_log = QTextEdit()
        self.text_teaching_log.setObjectName("teaching_log")
        self.text_teaching_log.setReadOnly(True)
        self.text_teaching_log.document().setMaximumBlockCount(0)
        self.text_teaching_log.setMinimumHeight(140)
        teaching_log_layout.addWidget(self.text_teaching_log)
        clear_teaching_log = QPushButton("Clear Log")
        clear_teaching_log.clicked.connect(self.text_teaching_log.clear)
        teaching_log_layout.addWidget(clear_teaching_log)
        right_layout.addWidget(teaching_log_group, 1)

        right_layout.addStretch()

        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("settings_scroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings_scroll.setMinimumWidth(300)
        settings_scroll.setMaximumWidth(390)
        settings_scroll.setWidget(right_panel)
        splitter.addWidget(settings_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 340])

        layout.addWidget(splitter)
        return widget

    def _build_io_tab(self) -> QWidget:
        """Build a dedicated tab for TCP and COM connections."""
        widget = QWidget()
        outer_layout = QHBoxLayout(widget)
        outer_layout.setContentsMargins(8, 8, 8, 8)

        content = QWidget()
        content.setObjectName("io_panel")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 4, 8, 8)
        content_layout.setSpacing(10)
        content_layout.addWidget(self.io_connection_group)

        bypass_group = QGroupBox("Bypass Control")
        bypass_layout = QVBoxLayout(bypass_group)
        bypass_warning = QLabel(
            "Maintenance use only. BYPASS captures and saves an image, skips model inspection, and forces every valid trigger to OK."
        )
        bypass_warning.setWordWrap(True)
        bypass_layout.addWidget(bypass_warning)

        self.btn_bypass = QPushButton("BYPASS: OFF")
        self.btn_bypass.setObjectName("btn_bypass")
        self.btn_bypass.setCheckable(True)
        self.btn_bypass.setToolTip("Capture and save an image, skip model inspection, and force OK for every valid trigger")
        self.btn_bypass.clicked.connect(self._on_toggle_bypass)
        bypass_layout.addWidget(self.btn_bypass)
        content_layout.addWidget(bypass_group)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("io_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return widget

    # ── Auto Tab ──
    def _build_developer_tab(self) -> QWidget:
        widget = QWidget()
        outer_layout = QVBoxLayout(widget)
        outer_layout.setContentsMargins(4, 4, 4, 4)
        outer_layout.setSpacing(6)

        setup_group = QGroupBox("PLC/PIC Folder Simulation")
        setup_layout = QGridLayout(setup_group)
        setup_layout.setContentsMargins(8, 8, 8, 8)
        setup_layout.setHorizontalSpacing(6)
        setup_layout.setVerticalSpacing(5)

        setup_layout.addWidget(QLabel("Image folder:"), 0, 0)
        self.line_developer_folder = QLineEdit()
        self.line_developer_folder.setReadOnly(True)
        self.line_developer_folder.setPlaceholderText("Select a test image folder")
        setup_layout.addWidget(self.line_developer_folder, 0, 1)

        self.btn_developer_browse = QPushButton("Browse...")
        self.btn_developer_browse.clicked.connect(self._on_select_developer_folder)
        setup_layout.addWidget(self.btn_developer_browse, 0, 2)

        options_widget = QWidget()
        options_layout = QHBoxLayout(options_widget)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(6)

        options_layout.addWidget(QLabel("Trigger:"))
        self.line_developer_trigger = QLineEdit("Test")
        self.line_developer_trigger.setMinimumWidth(75)
        self.line_developer_trigger.setMaximumWidth(110)
        self.line_developer_trigger.setToolTip(
            "A TCP/COM message matching this value starts one complete folder run."
        )
        options_layout.addWidget(self.line_developer_trigger)

        options_layout.addWidget(QLabel("Cycle delay:"))
        self.spin_developer_delay = QSpinBox()
        self.spin_developer_delay.setRange(0, 60000)
        self.spin_developer_delay.setValue(500)
        self.spin_developer_delay.setSuffix(" ms")
        self.spin_developer_delay.setToolTip("Delay between two simulated Auto cycles")
        options_layout.addWidget(self.spin_developer_delay)

        self.chk_developer_recursive = QCheckBox("Include subfolders")
        self.chk_developer_recursive.setChecked(True)
        options_layout.addWidget(self.chk_developer_recursive)
        options_layout.addStretch()

        self.btn_developer_arm = QPushButton("Arm Server")
        self.btn_developer_arm.setObjectName("btn_developer_arm")
        self.btn_developer_arm.clicked.connect(self._on_arm_developer)
        options_layout.addWidget(self.btn_developer_arm)

        self.btn_developer_stop = QPushButton("Stop Developer")
        self.btn_developer_stop.setObjectName("btn_developer_stop")
        self.btn_developer_stop.setEnabled(False)
        self.btn_developer_stop.clicked.connect(self._on_stop_developer)
        options_layout.addWidget(self.btn_developer_stop)
        setup_layout.addWidget(options_widget, 1, 0, 1, 3)

        self.developer_progress = QProgressBar()
        self.developer_progress.setRange(0, 1)
        self.developer_progress.setValue(0)
        self.developer_progress.setFormat("Waiting for configuration")
        setup_layout.addWidget(self.developer_progress, 2, 0, 1, 2)

        self.btn_developer_continuous = QPushButton("Continuous: OFF")
        self.btn_developer_continuous.setObjectName("btn_developer_continuous")
        self.btn_developer_continuous.setCheckable(True)
        self.btn_developer_continuous.setToolTip(
            "After one folder is complete, start again from the first image."
        )
        self.btn_developer_continuous.toggled.connect(
            self._on_toggle_developer_continuous
        )
        setup_layout.addWidget(self.btn_developer_continuous, 2, 2)
        setup_layout.setColumnStretch(1, 1)
        outer_layout.addWidget(setup_group)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.developer_canvas_container = QWidget()
        self.developer_canvas_container.setObjectName("canvas_card")
        self.developer_canvas_container.setProperty("resultStatus", "wait")
        canvas_layout = QVBoxLayout(self.developer_canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(5)

        canvas_header = QFrame()
        canvas_header.setObjectName("canvas_header")
        canvas_header_layout = QHBoxLayout(canvas_header)
        canvas_header_layout.setContentsMargins(10, 5, 10, 5)
        canvas_title = QLabel("DEVELOPER SIMULATION")
        canvas_title.setObjectName("canvas_title")
        canvas_header_layout.addWidget(canvas_title)
        canvas_header_layout.addStretch()
        self.label_developer_file = QLabel("No image")
        self.label_developer_file.setObjectName("canvas_hint")
        canvas_header_layout.addWidget(self.label_developer_file)
        canvas_layout.addWidget(canvas_header)

        self.developer_canvas = Canvas()
        self.developer_canvas.setMinimumSize(320, 220)
        canvas_layout.addWidget(self.developer_canvas, 1)
        splitter.addWidget(self.developer_canvas_container)

        results_panel = QWidget()
        results_panel.setObjectName("results_panel")
        results_layout = QVBoxLayout(results_panel)
        results_layout.setContentsMargins(6, 6, 6, 6)
        results_layout.setSpacing(7)

        self.developer_status_frame = QFrame()
        self.developer_status_frame.setObjectName("status_frame")
        self.developer_status_frame.setProperty("resultStatus", "wait")
        self.developer_status_frame.setMinimumHeight(82)
        status_layout = QVBoxLayout(self.developer_status_frame)
        self.label_developer_status = QLabel("DISARMED")
        self.label_developer_status.setObjectName("label_status_wait")
        self.label_developer_status.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.label_developer_status)
        results_layout.addWidget(self.developer_status_frame)

        self.label_developer_summary = QLabel("OK: 0   NG: 0   ERROR: 0")
        self.label_developer_summary.setAlignment(Qt.AlignCenter)
        results_layout.addWidget(self.label_developer_summary)

        self.developer_log_table = QTableWidget()
        self.developer_log_table.setColumnCount(5)
        self.developer_log_table.setHorizontalHeaderLabels(
            ["#", "File", "Result", "Detail", "Duration"]
        )
        self.developer_log_table.verticalHeader().setVisible(False)
        self.developer_log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.developer_log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.developer_log_table.setAlternatingRowColors(True)
        results_layout.addWidget(self.developer_log_table, 1)

        self.btn_developer_clear = QPushButton("Clear Results")
        self.btn_developer_clear.clicked.connect(self._clear_developer_results)
        results_layout.addWidget(self.btn_developer_clear)

        results_panel.setMinimumWidth(340)
        results_panel.setMaximumWidth(470)
        splitter.addWidget(results_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([700, 420])
        outer_layout.addWidget(splitter, 1)
        return widget

    # -- Auto Tab ------------------------------------------------------
    def _build_auto_tab(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Horizontal)

        # Left: Detection result canvas
        self.auto_canvas_container = QWidget()
        self.auto_canvas_container.setObjectName("canvas_card")
        self.auto_canvas_container.setProperty("resultStatus", "wait")
        auto_canvas_layout = QVBoxLayout(self.auto_canvas_container)
        auto_canvas_layout.setContentsMargins(0, 0, 0, 0)
        auto_canvas_layout.setSpacing(5)

        auto_canvas_header = QFrame()
        auto_canvas_header.setObjectName("canvas_header")
        auto_header_layout = QHBoxLayout(auto_canvas_header)
        auto_header_layout.setContentsMargins(10, 5, 10, 5)
        auto_title = QLabel("LIVE INSPECTION")
        auto_title.setObjectName("canvas_title")
        auto_header_layout.addWidget(auto_title)
        auto_header_layout.addStretch()
        auto_hint = QLabel("Live result viewport")
        auto_hint.setObjectName("canvas_hint")
        auto_header_layout.addWidget(auto_hint)
        auto_canvas_layout.addWidget(auto_canvas_header)

        self.auto_canvas = Canvas()
        self.auto_canvas.setMinimumSize(320, 240)
        auto_canvas_layout.addWidget(self.auto_canvas, 1)
        splitter.addWidget(self.auto_canvas_container)
        splitter.setChildrenCollapsible(False)

        # Right: Results panel
        right_panel = QWidget()
        right_panel.setObjectName("results_panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 6, 6, 6)
        right_layout.setSpacing(7)

        # Big status display
        self.status_frame = QFrame()
        self.status_frame.setObjectName("status_frame")
        self.status_frame.setProperty("resultStatus", "wait")
        self.status_frame.setMinimumHeight(105)
        self.status_frame.setMaximumHeight(140)
        status_inner = QVBoxLayout(self.status_frame)
        status_inner.setAlignment(Qt.AlignCenter)

        self.label_big_status = QLabel("WAIT")
        self.label_big_status.setObjectName("label_status_wait")
        self.label_big_status.setAlignment(Qt.AlignCenter)
        status_inner.addWidget(self.label_big_status)

        right_layout.addWidget(self.status_frame)

        # Compact counter strip. Keep the values prominent without spending
        # vertical space on a titled group box.
        counter_group = QFrame()
        counter_group.setObjectName("counter_strip")
        counter_layout = QGridLayout(counter_group)
        counter_layout.setContentsMargins(8, 4, 8, 4)
        counter_layout.setHorizontalSpacing(8)
        counter_layout.setVerticalSpacing(2)

        ok_caption = QLabel("OK")
        ok_caption.setAlignment(Qt.AlignCenter)
        counter_layout.addWidget(ok_caption, 0, 0)
        self.label_ok_count = QLabel("0")
        self.label_ok_count.setAlignment(Qt.AlignCenter)
        self.label_ok_count.setStyleSheet("font-size: 22px; font-weight: 700; color: #15803d;")
        counter_layout.addWidget(self.label_ok_count, 1, 0)

        ng_caption = QLabel("NG")
        ng_caption.setAlignment(Qt.AlignCenter)
        counter_layout.addWidget(ng_caption, 0, 1)
        self.label_ng_count = QLabel("0")
        self.label_ng_count.setAlignment(Qt.AlignCenter)
        self.label_ng_count.setStyleSheet("font-size: 22px; font-weight: 700; color: #dc2626;")
        counter_layout.addWidget(self.label_ng_count, 1, 1)

        total_caption = QLabel("Total")
        total_caption.setAlignment(Qt.AlignCenter)
        counter_layout.addWidget(total_caption, 0, 2)
        self.label_total_count = QLabel("0")
        self.label_total_count.setAlignment(Qt.AlignCenter)
        self.label_total_count.setStyleSheet("font-size: 22px; font-weight: 700; color: #2563eb;")
        counter_layout.addWidget(self.label_total_count, 1, 2)

        self.btn_reset_counter = QPushButton("Reset Counter")
        self.btn_reset_counter.clicked.connect(self._on_reset_counter)
        counter_layout.addWidget(self.btn_reset_counter, 0, 3, 2, 1)

        for column in range(3):
            counter_layout.setColumnStretch(column, 1)

        right_layout.addWidget(counter_group)

        # Inspection history (the table is self-explanatory, so no title is
        # needed). A minimum height prevents it collapsing to a single row.
        self.log_table = QTableWidget()
        self.log_table.setObjectName("inspection_log")
        self.log_table.setMinimumHeight(125)
        self.log_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.log_table.setColumnCount(4)
        self.log_table.setHorizontalHeaderLabels(["Time", "Result", "Detail", "Duration"])
        self.log_table.verticalHeader().setVisible(False)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log_table.setAlternatingRowColors(True)
        right_layout.addWidget(self.log_table, 1)

        # System log
        sys_log_group = QGroupBox("System Log")
        sys_log_inner = QVBoxLayout(sys_log_group)
        self.text_sys_log = QTextEdit()
        self.text_sys_log.setReadOnly(True)
        self.text_sys_log.document().setMaximumBlockCount(0)
        self.text_sys_log.setMaximumHeight(75 if self._compact_ui else 100)
        sys_log_inner.addWidget(self.text_sys_log)
        right_layout.addWidget(sys_log_group)

        right_panel.setMinimumWidth(300)
        right_panel.setMaximumWidth(390)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([760, 340])

        layout.addWidget(splitter)
        return widget

    # ================================================================
    # SIGNAL CONNECTIONS
    # ================================================================
    def _connect_signals(self):
        bus.show_image.connect(self._on_show_image)
        bus.show_status.connect(self._on_show_status)
        bus.hardware_status.connect(self._on_hardware_status)
        bus.log_message.connect(self._on_log_message)
        bus.write_log.connect(self._on_write_log)
        bus.auto_started.connect(self._on_auto_state_changed)
        bus.inspection_triggered.connect(self.image_retention.notify_trigger)
        # Listen to TCP directly so COM messages cannot start a developer batch.
        self.hardware_controller.server.sendData.connect(self._on_developer_trigger)
        self.tab_widget.currentChanged.connect(self._on_main_tab_changed)

    def _on_main_tab_changed(self, index: int):
        """Require Teaching authentication once during each application session."""
        if self._handling_protected_tab_change:
            return
        if self.is_auto_running and self.tab_widget.widget(index) is not self.tab_auto:
            self._handling_protected_tab_change = True
            try:
                self.tab_widget.setCurrentWidget(self.tab_auto)
            finally:
                self._handling_protected_tab_change = False
            return
        if self.tab_widget.widget(index) is not self.tab_teaching:
            self._last_authorized_tab_index = index
            return
        if self._teaching_unlocked:
            self._last_authorized_tab_index = index
            return

        password, accepted = QInputDialog.getText(
            self,
            "Teaching Access",
            "Enter Teaching password:",
            QLineEdit.Password,
        )
        if accepted and teaching_password_is_valid(password):
            self._teaching_unlocked = True
            self._last_authorized_tab_index = index
            bus.log_message.emit("Teaching access unlocked.", "INF")
            return

        if accepted:
            QMessageBox.warning(self, "Teaching Access", "Incorrect password.")
            bus.log_message.emit("Teaching access denied: incorrect password.", "WRN")
        else:
            bus.log_message.emit("Teaching access cancelled.", "INF")

        self._handling_protected_tab_change = True
        try:
            self.tab_widget.setCurrentIndex(self._last_authorized_tab_index)
        finally:
            self._handling_protected_tab_change = False

    # ================================================================
    # CONFIG MANAGEMENT
    # ================================================================
    def _load_initial_config(self):
        # Populate model list
        models = self.config_controller.list_models()
        self.combo_model.clear()
        self.combo_model.addItems(models)

        # Populate AI models
        self._refresh_ai_models()

        # Populate COM ports
        self._refresh_com_ports()

        # Load default
        if "Default" in models:
            self.combo_model.setCurrentText("Default")
        self._on_load_config(show_popup=False)

    def _refresh_com_ports(self):
        ports = list(dict.fromkeys(
            port.device for port in serial.tools.list_ports.comports() if port.device
        ))

        # Both COM selectors remain editable for manually configured devices.
        light_current = self.combo_light_com.currentText().strip()
        self.combo_light_com.clear()
        self.combo_light_com.addItems(ports)
        if light_current and self.combo_light_com.findText(light_current) == -1:
            self.combo_light_com.addItem(light_current)
        if light_current:
            self.combo_light_com.setCurrentText(light_current)

        plc_current = self.combo_plc_com.currentText().strip()
        self.combo_plc_com.clear()
        self.combo_plc_com.addItems(ports)
        if plc_current and self.combo_plc_com.findText(plc_current) == -1:
            self.combo_plc_com.addItem(plc_current)
        plc_index = self.combo_plc_com.findText(plc_current)
        self.combo_plc_com.setCurrentIndex(plc_index)
        if ports:
            self.combo_plc_com.setToolTip(
                f"Detected: {', '.join(ports)}. You can also enter a COM port manually."
            )
        else:
            self.combo_plc_com.setToolTip(
                "No COM ports detected. You can enter a COM port manually or click Refresh."
            )

    def _refresh_ai_models(self):
        models = self.config_controller.list_ai_models()
        current = self.combo_yolo_model.currentText()
        self.combo_yolo_model.clear()
        self.combo_yolo_model.addItems(models)
        if current and self.combo_yolo_model.findText(current) != -1:
            self.combo_yolo_model.setCurrentText(current)

    def _refresh_camera_devices(self):
        camera_type = self.combo_camera_type.currentText()
        if camera_type == "Basler":
            camera_type = "SODA"
        devices = get_camera_devices(camera_type)
        self.combo_camera_id.clear()
        for sn, name in devices.items():
            self.combo_camera_id.addItem(f"{name} ({sn})", sn)

    def _on_load_config(self, show_popup=False):
        if self.is_auto_running:
            if show_popup:
                QMessageBox.warning(self, "Load Model", "Stop Auto mode before loading settings.")
            return
        model_name = self.combo_model.currentText()
        if not model_name:
            if show_popup:
                QMessageBox.warning(self, "Load Model", "Please select a model configuration.")
            return
        try:
            self.current_config = self.config_controller.load_config(model_name)
            self._config_to_ui(self.current_config)
            self._apply_image_retention_config(self.current_config)
            bus.log_message.emit(f"Loaded model settings: {model_name}", "INF")
            if show_popup:
                QMessageBox.information(self, "Model Loaded", f"Model '{model_name}' loaded successfully.")
        except Exception as exc:
            bus.log_message.emit(f"Failed to load model '{model_name}': {exc}", "ERR")
            QMessageBox.critical(self, "Load Failed", str(exc))

    def _on_save_config(self):
        return self._save_config(confirm_overwrite=True, show_success=True)

    def _on_new_config(self):
        if self.is_auto_running:
            QMessageBox.warning(
                self, "New Model", "Stop Auto mode before creating a model."
            )
            return

        model_name, accepted = QInputDialog.getText(
            self,
            "New Model",
            "Enter a name for the new model configuration:",
        )
        if not accepted:
            return

        try:
            model_name = self.config_controller.validate_model_name(model_name)
            config = self._config_from_ui()
            config.shapes = self._get_shapes_from_canvas()
            if len(config.shapes) > 1:
                QMessageBox.warning(
                    self,
                    "New Model",
                    "A model can contain at most one ROI. Clear the extra ROIs first.",
                )
                return
            config = self.config_controller.add_model(model_name, config)
        except (ValueError, FileExistsError) as exc:
            QMessageBox.warning(self, "New Model", str(exc))
            return
        except Exception as exc:
            bus.log_message.emit(f"Failed to create model '{model_name}': {exc}", "ERR")
            QMessageBox.critical(self, "New Model", str(exc))
            return

        self.current_config = config
        models = self.config_controller.list_models()
        self.combo_model.clear()
        self.combo_model.addItems(models)
        self.combo_model.setCurrentText(model_name)
        self._apply_image_retention_config(config)
        bus.log_message.emit(f"Created model settings: {model_name}", "INF")
        QMessageBox.information(
            self,
            "New Model",
            f"Model configuration '{model_name}' was created and selected.",
        )

    def _on_save_shortcut(self):
        if self.is_auto_running:
            self.statusBar().showMessage("Stop Auto mode before saving configuration", 3000)
            return
        if self._save_config(confirm_overwrite=False, show_success=False):
            self.statusBar().showMessage("Configuration saved (Ctrl+S)", 3000)

    def _save_config(self, confirm_overwrite=True, show_success=True) -> bool:
        if self.is_auto_running:
            if show_success:
                QMessageBox.warning(self, "Save Model", "Stop Auto mode before saving settings.")
            return False
        model_name = self.combo_model.currentText()
        if not model_name:
            QMessageBox.warning(self, "Save Model", "Please select a model configuration.")
            return False
        config = self._config_from_ui()
        # Save shapes from canvas
        config.shapes = self._get_shapes_from_canvas()
        if len(config.shapes) > 1:
            QMessageBox.warning(
                self, "Save Model",
                "A model can contain at most one ROI. Clear the extra ROIs before saving."
            )
            return False
        if confirm_overwrite:
            answer = QMessageBox.question(
                self, "Save Model", f"Overwrite settings for model '{model_name}'?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                bus.log_message.emit(f"Save cancelled: {model_name}", "INF")
                return False
        try:
            self.config_controller.save_config(model_name, config)
            self.current_config = config
            self._apply_image_retention_config(config)
            bus.log_message.emit(f"Saved model settings: {model_name}", "INF")
            if show_success:
                QMessageBox.information(self, "Model Saved", f"Model '{model_name}' saved successfully.")
            return True
        except Exception as exc:
            bus.log_message.emit(f"Failed to save model '{model_name}': {exc}", "ERR")
            QMessageBox.critical(self, "Save Failed", str(exc))
            return False

    def _config_from_ui(self) -> AppConfig:
        from app.models.config import (
            ModelAIConfig, ServerConfig, CameraConfig,
            LightingConfig, SystemConfig, SerialConfig, HardwareConfig,
        )
        trigger_code = self.line_trigger_code.text().strip() or "L1"
        current_system = (
            self.current_config.hardware.system
            if self.current_config is not None
            else SystemConfig()
        )
        config = AppConfig(
            name_model=self.combo_model.currentText(),
            shapes=self._get_shapes_from_canvas(),
            model_ai=ModelAIConfig(
                model_name=self.combo_yolo_model.currentText(),
                conf=self.spin_conf.value(),
                iou=self.spin_iou.value(),
                max_det=self.spin_max_det.value(),
            ),
            hardware=HardwareConfig(
                server=ServerConfig(
                    ip=self.line_tcp_ip.text(),
                    port=int(self.line_tcp_port.text()) if self.line_tcp_port.text().isdigit() else 5000,
                ),
                camera=CameraConfig(
                    id=self.combo_camera_id.currentData() or self.combo_camera_id.currentText(),
                    name=self.combo_camera_type.currentText(),
                    delay=self.spin_cam_delay.value(),
                ),
                lighting=LightingConfig(
                    comport=self.combo_light_com.currentText(),
                    baudrate=int(self.combo_light_baud.currentText()),
                    channel_value_0=self.spin_light_ch0.value(),
                    channel_value_1=self.spin_light_ch1.value(),
                    channel_value_2=self.spin_light_ch2.value(),
                    channel_value_3=self.spin_light_ch3.value(),
                    controller=self.combo_light_type.currentText(),
                    delay=self.spin_light_delay.value(),
                ),
                system=SystemConfig(
                    log_dir=current_system.log_dir,
                    log_size=current_system.log_size,
                    image_retention=current_system.image_retention.model_copy(deep=True),
                    database_path=current_system.database_path,
                    code_com_check=[trigger_code],
                    code_tcp_check=[trigger_code],
                    use_com_send_data=self.chk_use_com.isChecked(),
                    use_tcp_send_data=self.chk_use_tcp.isChecked(),
                ),
                send_data=SerialConfig(
                    comport=self.combo_plc_com.currentText(),
                    baudrate=int(self.combo_plc_baud.currentText()),
                ),
            ),
        )
        return config

    def _apply_image_retention_config(self, config: AppConfig):
        system_config = config.hardware.system
        self.image_retention.configure(
            get_abs_path(system_config.log_dir),
            RetentionPolicy.from_config(system_config.image_retention),
        )

    def _config_to_ui(self, config: AppConfig):
        # Camera
        cam_name = config.hardware.camera.name
        if cam_name in ("SODA", "Basler"):
            cam_name = "Basler"
        if self.combo_camera_type.findText(cam_name) != -1:
            self.combo_camera_type.setCurrentText(cam_name)
        self._refresh_camera_devices()
        self.combo_camera_id.setCurrentText(config.hardware.camera.id)
        self.spin_cam_delay.setValue(config.hardware.camera.delay)

        # YOLO
        if config.model_ai.model_name:
            self.combo_yolo_model.setCurrentText(config.model_ai.model_name)
        self.spin_conf.setValue(config.model_ai.conf)
        self.spin_iou.setValue(config.model_ai.iou)
        self.spin_max_det.setValue(config.model_ai.max_det)

        # Light
        self.combo_light_type.setCurrentText(config.hardware.lighting.controller)
        self._set_combo(self.combo_light_com, config.hardware.lighting.comport)
        self.combo_light_baud.setCurrentText(str(config.hardware.lighting.baudrate))
        self.spin_light_ch0.setValue(config.hardware.lighting.channel_value_0)
        self.spin_light_ch1.setValue(config.hardware.lighting.channel_value_1)
        self.spin_light_ch2.setValue(config.hardware.lighting.channel_value_2)
        self.spin_light_ch3.setValue(config.hardware.lighting.channel_value_3)
        self.spin_light_delay.setValue(config.hardware.lighting.delay)

        # I/O
        self.line_tcp_ip.setText(config.hardware.server.ip)
        self.line_tcp_port.setText(str(config.hardware.server.port))
        saved_plc_com = (config.hardware.send_data.comport or "").strip()
        self._set_combo(self.combo_plc_com, saved_plc_com)
        self.combo_plc_baud.setCurrentText(str(config.hardware.send_data.baudrate))
        self.chk_use_tcp.setChecked(config.hardware.system.use_tcp_send_data)
        self.chk_use_com.setChecked(config.hardware.system.use_com_send_data)

        trigger_codes = config.hardware.system.code_tcp_check
        if isinstance(trigger_codes, list):
            self.line_trigger_code.setText(", ".join(trigger_codes))
        else:
            self.line_trigger_code.setText(str(trigger_codes))

        # Shapes / ROI
        self.canvas.clear_shapes()
        if config.shapes:
            for name, roi in config.shapes.items():
                if len(roi) == 4:
                    shape = MyShape(name=name)
                    x, y, w, h = roi
                    shape.addPoint(x, y)
                    shape.addPoint(x + w, y)
                    shape.addPoint(x + w, y + h)
                    shape.addPoint(x, y + h)
                    self.canvas.add_shape(shape)

    @staticmethod
    def _set_combo(combo, value):
        value = (value or "").strip()
        if not value:
            return
        if combo.findText(value) == -1:
            combo.addItem(value)
        combo.setCurrentText(value)

    def _get_shapes_from_canvas(self) -> dict:
        shapes = {}
        for shape in self.canvas.get_shapes():
            if len(shape.points) >= 4:
                pts = shape.points
                x = min(p.x() for p in pts)
                y = min(p.y() for p in pts)
                x2 = max(p.x() for p in pts)
                y2 = max(p.y() for p in pts)
                shapes[shape.name] = [x, y, x2 - x, y2 - y]
        return shapes

    # ================================================================
    # CAMERA
    # ================================================================
    def _on_open_camera(self):
        cam_type = self.combo_camera_type.currentText()
        if cam_type == "Basler":
            cam_type = "SODA"
        cam_id = self.combo_camera_id.currentData() or self.combo_camera_id.currentText()
        success = self.hardware_controller.open_camera(cam_type, cam_id)
        if success:
            self._set_crop_source_folder(None)
            self._static_image_mode = False
            self.btn_open_cam.setText("READY")
            self.btn_open_cam.setEnabled(False)
            self.btn_close_cam.setEnabled(True)
            self.btn_capture.setText("Capture")
            self.btn_live_camera.setEnabled(True)
            self.statusBar().showMessage("Camera READY - acquisition stopped")

    def _on_close_camera(self):
        if not self._stop_camera_preview():
            QMessageBox.warning(
                self, "Camera",
                "Camera preview is still busy and could not be disconnected safely."
            )
            return
        self.hardware_controller.close_camera()
        self.btn_open_cam.setText("Connect")
        self.btn_open_cam.setEnabled(True)
        self.btn_close_cam.setEnabled(False)
        self._set_live_button(False)
        self.btn_live_camera.setEnabled(False)

    def _start_camera_preview(self):
        if self.camera_worker is not None:
            if not self._stop_camera_preview():
                return False
        if not self.hardware_controller.start_camera_acquisition():
            return False
        self.camera_worker = CameraWorker(self.hardware_controller.camera, delay_ms=40)
        self.camera_worker.frame_ready.connect(self._on_camera_frame)
        self.camera_worker.error_occurred.connect(
            lambda err: bus.log_message.emit(f"Camera: {err}", "WRN")
        )
        self.camera_worker.start()
        return True

    def _stop_camera_preview(self):
        if self.camera_worker is not None:
            worker = self.camera_worker
            if worker.stop():
                self.camera_worker = None
                self.hardware_controller.stop_camera_acquisition()
                self._set_live_button(False)
                return True
            bus.log_message.emit("Camera preview did not stop within 3 seconds.", "ERR")
            return False
        return True

    def _set_live_button(self, enabled: bool):
        if not hasattr(self, "btn_live_camera"):
            return
        self.btn_live_camera.setChecked(bool(enabled))
        self.btn_live_camera.setText("LIVE: ON" if enabled else "LIVE: OFF")

    def _on_toggle_live_camera(self, enabled: bool):
        if self.is_auto_running:
            self._set_live_button(False)
            QMessageBox.warning(self, "Live Camera", "Live view is disabled while Auto is running.")
            return
        if enabled:
            if not self.hardware_controller.is_open_camera:
                self._set_live_button(False)
                QMessageBox.warning(self, "Live Camera", "Connect the camera first.")
                return
            if not self._start_camera_preview():
                self._set_live_button(False)
                QMessageBox.warning(self, "Live Camera", "Could not start camera live view.")
                return
            self._set_live_button(True)
            self._static_image_mode = False
            self.statusBar().showMessage("Camera LIVE")
            bus.log_message.emit("Camera live view started.", "INF")
        else:
            if not self._stop_camera_preview():
                self._set_live_button(True)
                QMessageBox.warning(self, "Live Camera", "Camera live view is still stopping.")
                return
            self._set_live_button(False)
            self.statusBar().showMessage("Camera READY - live view stopped")
            bus.log_message.emit("Camera live view stopped; camera READY.", "INF")

    def _on_camera_frame(self, frame, source_worker=None):
        try:
            # Ignore frames already queued before Teaching switched to a frozen image.
            if self.is_auto_running or self._static_image_mode:
                return
            self.current_frame = frame
            self._display_frame(frame, self.canvas)
        finally:
            if source_worker is not None:
                source_worker.mark_frame_consumed()

    def _display_frame(self, frame, canvas_widget, fit=None):
        if frame is None:
            return
        h, w = frame.shape[:2]
        if len(frame.shape) == 2:
            qimg = QImage(frame.data, w, h, frame.strides[0], QImage.Format_Grayscale8)
        else:
            bgr_format = getattr(QImage, "Format_BGR888", None)
            if bgr_format is not None:
                qimg = QImage(frame.data, w, h, frame.strides[0], bgr_format)
            else:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)
        canvas_widget.load_pixmap(pixmap, fit=fit)

    # ================================================================
    # ROI DRAWING
    # ================================================================
    def _on_draw_roi(self):
        if self.camera_worker is not None:
            if not self._stop_camera_preview():
                QMessageBox.warning(self, "Draw ROI", "Camera preview is still busy. Please try again.")
                return
            self._static_image_mode = True
            self._set_live_button(False)
            bus.log_message.emit("Camera preview frozen for ROI teaching.", "INF")
        self.canvas.set_editing_mode(True)
        bus.log_message.emit(
            "Draw ROI mode enabled. Click and drag to add an ROI; use a unique name for each ROI.",
            "INF",
        )

    def _on_clear_roi(self):
        if not self.canvas.get_shapes():
            bus.log_message.emit("There is no ROI to clear.", "WRN")
            return
        answer = QMessageBox.question(
            self, "Clear ROI", "Remove the current ROI?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.canvas.clear_shapes()
        bus.log_message.emit("All ROIs cleared.", "INF")

    # ================================================================
    # CAPTURE & TEST
    # ================================================================
    def _on_capture(self):
        if self.is_auto_running:
            QMessageBox.warning(self, "Capture", "Manual capture is disabled while Auto mode is running.")
            return
        if not self.hardware_controller.is_open_camera:
            QMessageBox.warning(self, "Capture", "Connect the camera first (READY state).")
            return

        if self.camera_worker is not None:
            if not self._stop_camera_preview():
                QMessageBox.warning(self, "Capture", "Live view is still stopping. Please try again.")
                return
            self._set_live_button(False)

        bus.log_message.emit("Single capture started from camera READY state.", "INF")
        frame = self.hardware_controller.capture_single_frame()
        if frame is None:
            bus.log_message.emit("Single capture failed; camera returned to READY.", "ERR")
            QMessageBox.warning(self, "Capture", "Camera did not return a frame.")
            return

        self.current_frame = frame.copy()
        self._set_crop_source_folder(None)
        self._static_image_mode = True
        self._display_frame(frame, self.canvas, fit=True)
        try:
            capture_path = save_captured_image(frame)
            bus.log_message.emit(
                f"Captured image saved: {capture_path.replace(chr(92), '/')}",
                "INF",
            )
        except Exception as save_ex:
            # A storage failure must not discard a frame that was captured successfully.
            bus.log_message.emit(f"Failed to save captured image: {save_ex}", "ERR")
            QMessageBox.warning(
                self,
                "Capture",
                f"The frame was captured but could not be saved:\n{save_ex}",
            )
        self.statusBar().showMessage("Camera READY - single frame captured")
        bus.log_message.emit("Frame captured; camera returned to READY.", "INF")

    def _on_load_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Image", "", "Images (*.png *.jpg *.bmp *.jpeg *.tif *.tiff)"
        )
        if path:
            img = read_image(path)
            if img is not None:
                if self.camera_worker is not None:
                    if not self._stop_camera_preview():
                        QMessageBox.warning(
                            self, "Load Image",
                            "Camera preview is still busy. Stop the camera and try again."
                        )
                        return
                self._static_image_mode = True
                self._set_crop_source_folder(None)
                self.btn_capture.setText("Capture")
                self._set_live_button(False)
                self.current_frame = img
                self._display_frame(img, self.canvas, fit=True)
                bus.log_message.emit(f"Loaded image: {path}", "INF")
            else:
                bus.log_message.emit(f"Cannot read image: {path}", "ERR")
                QMessageBox.critical(self, "Load Image", "The selected image could not be read.")

    def _on_select_crop_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Image Folder", "")
        if not folder:
            return

        images = list_images(folder)
        if not images:
            QMessageBox.warning(
                self, "Select Folder",
                "The selected folder contains no supported images."
            )
            return
        if self.camera_worker is not None and not self._stop_camera_preview():
            QMessageBox.warning(
                self, "Select Folder",
                "Camera preview is still busy. Stop the camera and try again."
            )
            return

        preview_path = None
        first_image = None
        for image_path in images:
            first_image = read_image(image_path)
            if first_image is not None:
                preview_path = image_path
                break
        if first_image is None:
            QMessageBox.critical(
                self, "Select Folder",
                "None of the images in the selected folder could be read."
            )
            return

        self._set_crop_source_folder(preview_path.parent)
        self._static_image_mode = True
        self.btn_capture.setText("Capture")
        self._set_live_button(False)
        self.current_frame = first_image
        self._display_frame(first_image, self.canvas, fit=True)
        self.statusBar().showMessage(
            f"Crop folder selected: {self._crop_source_folder} ({len(images)} images)"
        )
        bus.log_message.emit(
            f"Selected crop folder with {len(images)} images: {self._crop_source_folder}",
            "INF",
        )

    def _set_crop_source_folder(self, folder):
        self._crop_source_folder = folder
        if not hasattr(self, "crop_folder_label"):
            return
        if folder is None:
            self.crop_folder_label.setText("Folder: not selected")
            self.crop_folder_label.setToolTip("")
            return
        folder_text = str(folder)
        folder_name = os.path.basename(folder_text.rstrip("/\\")) or folder_text
        self.crop_folder_label.setText(f"Folder: {folder_name}")
        self.crop_folder_label.setToolTip(folder_text)

    def _on_crop_folder(self):
        if self._crop_source_folder is None:
            QMessageBox.warning(self, "Crop Folder", "Please select an image folder first.")
            return
        if self.current_frame is None:
            QMessageBox.warning(self, "Crop Folder", "No reference image is loaded.")
            return

        regions = []
        for index, shape in enumerate(self.canvas.get_shapes(), start=1):
            if shape.hide or len(shape.points) < 4:
                continue
            left = min(point.x() for point in shape.points)
            top = min(point.y() for point in shape.points)
            right = max(point.x() for point in shape.points)
            bottom = max(point.y() for point in shape.points)
            if right > left and bottom > top:
                regions.append(CropRegion(
                    shape.name or f"ROI_{index}", left, top, right - left, bottom - top
                ))
        if not regions:
            QMessageBox.warning(self, "Crop Folder", "Please draw at least one visible ROI first.")
            return

        image_height, image_width = self.current_frame.shape[:2]

        def update_progress(done, total, path):
            self.statusBar().showMessage(f"Cropping {done}/{total}: {path.name}")
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = crop_folder(
                self._crop_source_folder,
                regions,
                (image_width, image_height),
                progress=update_progress,
            )
        except Exception as exc:
            bus.log_message.emit(f"Crop folder failed: {exc}", "ERR")
            QMessageBox.critical(self, "Crop Folder", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()

        output_path = str(result.output_folder.resolve())
        message = (
            f"Processed {result.source_images} source images.\n"
            f"Saved {result.saved_images} cropped images to:\n{output_path}"
        )
        if result.failed_images:
            message += f"\n\nFailed crops: {result.failed_images}"
        self.statusBar().showMessage(
            f"Crop complete: {result.saved_images} images saved", 10000
        )
        bus.log_message.emit(message.replace("\n", " | "), "INF")
        QMessageBox.information(self, "Crop Folder Complete", message)

    def _on_test_detection(self):
        if self.is_developer_running:
            QMessageBox.warning(
                self,
                "Test Detection",
                "Wait for the Developer folder simulation to finish before running a manual test.",
            )
            return
        if self.current_frame is None:
            QMessageBox.warning(self, "Warning", "No image available. Capture or load an image first.")
            return

        config = self._config_from_ui()
        config.shapes = self._get_shapes_from_canvas()

        if len(config.shapes) > 1:
            QMessageBox.warning(self, "Warning", "Please keep at most one ROI.")
            return

        if not config.model_ai.model_name:
            QMessageBox.warning(self, "Warning", "Please select a YOLO model.")
            return

        try:
            result = detect_tape(
                self.current_frame,
                config,
                self.model_pool,
            )
            self._display_frame(result.dst, self.canvas)
            status = "OK ✅" if result.ret == "PASS" else "NG ❌"
            bus.log_message.emit(
                f"Test result: {status} | {result.label_counts} | {result.timecheck}ms",
                "INF",
            )
        except Exception as e:
            bus.log_message.emit(f"Test failed: {e}", "ERR")
            QMessageBox.critical(self, "Error", str(e))

    # ================================================================
    # HARDWARE CONNECTIONS
    # ================================================================
    def _on_open_light(self):
        ctrl_type = self.combo_light_type.currentText()
        com = self.combo_light_com.currentText()
        baud = int(self.combo_light_baud.currentText())
        if self.hardware_controller.open_light(ctrl_type, com, baud):
            self.btn_open_light.setEnabled(False)
            self.btn_close_light.setEnabled(True)
            self._set_light_button_state(False)
            self.btn_toggle_light.setEnabled(not self.is_auto_running)
        else:
            QMessageBox.warning(
                self, "Light Connection",
                "Could not connect to the light controller. Check the COM port and baud rate."
            )

    def _on_close_light(self):
        self.hardware_controller.close_light()
        self._set_light_button_state(False)
        self.btn_toggle_light.setEnabled(False)
        self.btn_open_light.setEnabled(True)
        self.btn_close_light.setEnabled(False)

    def _light_channel_values(self):
        return tuple(getattr(self, f"spin_light_ch{i}").value() for i in range(4))

    def _set_light_button_state(self, enabled: bool, text: str = None):
        self.btn_toggle_light.blockSignals(True)
        self.btn_toggle_light.setChecked(enabled)
        self.btn_toggle_light.blockSignals(False)
        self.btn_toggle_light.setText(text or ("LIGHT: ON" if enabled else "LIGHT: OFF"))
        self.btn_toggle_light.style().unpolish(self.btn_toggle_light)
        self.btn_toggle_light.style().polish(self.btn_toggle_light)

    def _on_toggle_light(self, enabled: bool):
        if not self.hardware_controller.is_open_light:
            self._set_light_button_state(False)
            QMessageBox.warning(self, "Light Controller", "Connect the light controller first.")
            return

        values = self._light_channel_values() if enabled else (0, 0, 0, 0)
        if enabled and not any(values):
            self._set_light_button_state(False)
            QMessageBox.warning(self, "Light Controller", "Set at least one channel above 0.")
            return

        if self.hardware_controller.set_light_values(*values, 0):
            self._set_light_button_state(enabled)
            bus.log_message.emit(f"Manual light switched {'ON' if enabled else 'OFF'}", "INF")
        else:
            self._set_light_button_state(not enabled)
            QMessageBox.warning(self, "Light Controller", "The light command was not accepted.")

    def _on_light_value_changed(self, _value: int):
        """Apply brightness changes immediately, matching the template behavior."""
        if (not hasattr(self, "btn_toggle_light") or
                not self.btn_toggle_light.isChecked() or
                self.is_auto_running or
                not self.hardware_controller.is_open_light):
            return
        if not self.hardware_controller.set_light_values(*self._light_channel_values(), 0):
            self._set_light_button_state(False)
            bus.log_message.emit("Live light value update failed; manual light switched OFF", "ERR")

    def _on_start_tcp(self):
        ip = self.line_tcp_ip.text().strip()
        port_text = self.line_tcp_port.text().strip()
        if not ip or not port_text.isdigit() or not (1 <= int(port_text) <= 65535):
            QMessageBox.warning(self, "TCP Connection", "Enter a valid IP address and port (1-65535).")
            return
        port = int(port_text)
        if self.hardware_controller.open_server(ip, port):
            self.btn_tcp_start.setEnabled(False)
            self.btn_tcp_stop.setEnabled(True)
        else:
            QMessageBox.warning(
                self, "TCP Connection",
                f"Could not start the TCP server at {ip}:{port}. Check the address and whether the port is in use."
            )

    def _on_stop_tcp(self):
        self.hardware_controller.close_server()
        self.btn_tcp_start.setEnabled(True)
        self.btn_tcp_stop.setEnabled(False)

    def _on_open_com(self):
        com = self.combo_plc_com.currentText().strip()
        if not com:
            QMessageBox.warning(
                self,
                "COM Connection",
                "Select a detected COM port or enter one manually.",
            )
            return
        baud = int(self.combo_plc_baud.currentText())
        if self.hardware_controller.open_send_data(com, baud):
            self.btn_com_open.setEnabled(False)
            self.btn_com_close.setEnabled(True)
            self.combo_plc_com.setEnabled(False)
            self.combo_plc_baud.setEnabled(False)
            self.btn_refresh_plc_com.setEnabled(False)
        else:
            QMessageBox.warning(
                self, "COM Connection",
                "Could not open the COM connection. Check the port and baud rate."
            )

    def _on_close_com(self):
        self.hardware_controller.close_send_data()
        self.btn_com_open.setEnabled(True)
        self.btn_com_close.setEnabled(False)
        self.combo_plc_com.setEnabled(True)
        self.combo_plc_baud.setEnabled(True)
        self.btn_refresh_plc_com.setEnabled(True)

    def _ensure_auto_camera_ready(self, config, bypass_enabled: bool) -> tuple:
        """Connect the configured camera for Auto when the operator has not done so."""
        self._auto_opened_camera = False

        if not self.hardware_controller.is_open_camera:
            camera_cfg = config.hardware.camera
            camera_type = "SODA" if camera_cfg.name == "Basler" else camera_cfg.name
            if not self.hardware_controller.open_camera(
                camera_type, camera_cfg.id, camera_cfg.feature
            ):
                return False, f"Could not connect camera '{camera_cfg.name}' ({camera_cfg.id})."
            self._auto_opened_camera = True
            self.btn_open_cam.setText("READY")
            self.btn_open_cam.setEnabled(False)
            self.btn_close_cam.setEnabled(True)
            self.btn_live_camera.setEnabled(False)

        if not self.hardware_controller.stop_camera_acquisition():
            self._stop_auto_owned_camera()
            return False, "Camera could not enter READY state."
        return True, ""

    def _stop_auto_owned_camera(self):
        if self._auto_opened_camera:
            self.hardware_controller.close_camera()
            self.btn_open_cam.setText("Connect")
            self.btn_open_cam.setEnabled(True)
            self.btn_close_cam.setEnabled(False)
            self.btn_live_camera.setEnabled(False)
            self._set_live_button(False)
        else:
            self.hardware_controller.stop_camera_acquisition()
        self._auto_opened_camera = False

    def _ensure_auto_light_ready(self, config, bypass_enabled: bool) -> tuple:
        """Connect the light for Auto, but keep every channel off until a trigger."""
        self._auto_opened_light = False

        if not self.hardware_controller.is_open_light:
            light_cfg = config.hardware.lighting
            if not self.hardware_controller.open_light(
                light_cfg.controller, light_cfg.comport, light_cfg.baudrate
            ):
                return False, (
                    f"Could not connect light controller on {light_cfg.comport} "
                    f"at {light_cfg.baudrate} baud."
                )
            self._auto_opened_light = True

        # Auto must always begin in a safe dark/READY state. ON_LIGHTING in
        # AutoController is the only state allowed to apply configured values.
        if not self.hardware_controller.set_light_values(0, 0, 0, 0, 0):
            self._stop_auto_owned_light()
            return False, "Light controller connected but could not switch all channels OFF."

        self.btn_open_light.setEnabled(False)
        self.btn_close_light.setEnabled(True)
        self._set_light_button_state(False, "LIGHT: AUTO")
        self.btn_toggle_light.setEnabled(False)
        bus.log_message.emit("Light controller READY; channels OFF while waiting for PLC trigger", "INF")
        return True, ""

    def _stop_auto_owned_light(self):
        """Leave the light off and close it only when this Auto session opened it."""
        if self.hardware_controller.is_open_light:
            self.hardware_controller.set_light_values(0, 0, 0, 0, 0)
        if self._auto_opened_light:
            self.hardware_controller.close_light()
        self._auto_opened_light = False
        self._set_light_button_state(False)
        self.btn_open_light.setEnabled(not self.hardware_controller.is_open_light)
        self.btn_close_light.setEnabled(self.hardware_controller.is_open_light)
        self.btn_toggle_light.setEnabled(self.hardware_controller.is_open_light)

    def _start_selected_auto_io(self, config) -> tuple:
        """Open selected I/O channels and remember which ones Auto owns."""
        self._auto_opened_io = {"tcp": False, "com": False}

        if config.hardware.system.use_tcp_send_data:
            if not self.hardware_controller.is_connect_server:
                server_cfg = config.hardware.server
                if not self.hardware_controller.open_server(server_cfg.ip, server_cfg.port):
                    return False, f"Could not start TCP at {server_cfg.ip}:{server_cfg.port}."
                self._auto_opened_io["tcp"] = True
            self.btn_tcp_start.setEnabled(False)
            self.btn_tcp_stop.setEnabled(True)

        if config.hardware.system.use_com_send_data:
            if not self.hardware_controller.is_com_send_data:
                serial_cfg = config.hardware.send_data
                if not self.hardware_controller.open_send_data(
                    serial_cfg.comport, serial_cfg.baudrate
                ):
                    self._stop_auto_owned_io()
                    return False, f"Could not open COM port {serial_cfg.comport}."
                self._auto_opened_io["com"] = True
            self.btn_com_open.setEnabled(False)
            self.btn_com_close.setEnabled(True)

        if not (config.hardware.system.use_tcp_send_data
                or config.hardware.system.use_com_send_data):
            return False, "Select at least one I/O channel (TCP or COM) for Auto mode."
        return True, ""

    def _stop_auto_owned_io(self):
        """Close only connections opened automatically by this Auto session."""
        if self._auto_opened_io.get("tcp"):
            self.hardware_controller.close_server()
            self.btn_tcp_start.setEnabled(True)
            self.btn_tcp_stop.setEnabled(False)
        if self._auto_opened_io.get("com"):
            self.hardware_controller.close_send_data()
            self.btn_com_open.setEnabled(True)
            self.btn_com_close.setEnabled(False)
            self.combo_plc_com.setEnabled(True)
            self.combo_plc_baud.setEnabled(True)
            self.btn_refresh_plc_com.setEnabled(True)
        self._auto_opened_io = {"tcp": False, "com": False}

    # ================================================================
    # AUTO INSPECTION
    # ================================================================
    def _on_select_developer_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Developer Test Folder", self.line_developer_folder.text()
        )
        if not folder:
            return
        images = list_images(folder, recursive=self.chk_developer_recursive.isChecked())
        if not images:
            QMessageBox.warning(
                self, "Developer Mode", "The selected folder contains no supported images."
            )
            return
        self._developer_folder = folder
        self._developer_images = images
        self.line_developer_folder.setText(folder)
        self.line_developer_folder.setToolTip(folder)
        self.developer_progress.setRange(0, len(images))
        self.developer_progress.setValue(0)
        self.developer_progress.setFormat(f"Ready: {len(images)} images")

        preview = read_image(images[0])
        if preview is not None:
            self._display_frame(preview, self.developer_canvas, fit=True)
            self.label_developer_file.setText(images[0].name)
            self.label_developer_file.setToolTip(str(images[0]))
        bus.log_message.emit(
            f"Developer folder selected: {folder} ({len(images)} images)", "INF"
        )

    def _on_arm_developer(self):
        if self.is_auto_running:
            QMessageBox.warning(
                self, "Developer Mode", "Stop Auto mode before arming Developer mode."
            )
            return
        if self.is_developer_armed:
            return
        if not self._developer_folder:
            QMessageBox.warning(self, "Developer Mode", "Select an image folder first.")
            return

        images = list_images(
            self._developer_folder,
            recursive=self.chk_developer_recursive.isChecked(),
        )
        if not images:
            QMessageBox.warning(
                self, "Developer Mode", "The selected folder contains no supported images."
            )
            return

        trigger = self.line_developer_trigger.text().strip()
        if not trigger:
            QMessageBox.warning(self, "Developer Mode", "Enter a trigger value (for example: Test).")
            return

        config = self._config_from_ui()
        config.shapes = self._get_shapes_from_canvas()
        if len(config.shapes) > 1:
            QMessageBox.warning(
                self, "Developer Mode", "Keep at most one ROI before starting the simulation."
            )
            return
        if not config.model_ai.model_name:
            QMessageBox.warning(self, "Developer Mode", "Select a YOLO model first.")
            return

        self._developer_opened_tcp = False
        if not self.hardware_controller.is_connect_server:
            server_cfg = config.hardware.server
            if not self.hardware_controller.open_server(server_cfg.ip, server_cfg.port):
                QMessageBox.critical(
                    self,
                    "Developer Mode",
                    f"Could not start TCP server at {server_cfg.ip}:{server_cfg.port}.",
                )
                return
            self._developer_opened_tcp = True

        self._developer_images = images
        self._developer_config = config
        self._developer_restart_pending = False
        self._developer_loop_count = 0
        self.current_config = config
        self.is_developer_armed = True
        self._set_developer_controls(True)
        self._set_developer_status("WAIT TEST", "wait")
        self.developer_progress.setRange(0, len(images))
        self.developer_progress.setValue(0)
        self.developer_progress.setFormat(
            f"Armed: waiting for '{trigger}' ({len(images)} images)"
        )
        self.tab_widget.setCurrentWidget(self.tab_developer)
        endpoint = f"{config.hardware.server.ip}:{config.hardware.server.port}"
        self.statusBar().showMessage(f"DEVELOPER: TCP {endpoint} waiting for '{trigger}'")
        bus.log_message.emit(
            f"Developer mode armed on TCP {endpoint}; waiting for trigger '{trigger}'", "INF"
        )

    def _on_developer_trigger(self, code: str):
        if not self.is_developer_armed:
            return
        expected = self.line_developer_trigger.text().strip()
        if code.strip().casefold() != expected.casefold():
            bus.log_message.emit(
                f"Developer mode ignored TCP trigger '{code}' (expected '{expected}')", "WRN"
            )
            return
        if self.developer_worker is not None and self.developer_worker.isRunning():
            self.hardware_controller.server.send_data_to_all_clients("BUSY")
            bus.log_message.emit("Developer trigger ignored: folder simulation is busy", "WRN")
            return

        self._developer_loop_count = 0
        self._start_developer_batch()
        bus.log_message.emit(
            f"Developer trigger '{code}' accepted; starting {len(self._developer_images)} images",
            "INF",
        )

    def _start_developer_batch(self):
        if not self.is_developer_armed:
            return
        if self.developer_worker is not None and self.developer_worker.isRunning():
            return

        self._developer_restart_pending = False
        self._developer_loop_count += 1
        self._clear_developer_results()
        self.is_developer_running = True
        self._set_developer_status("RUNNING", "wait")
        self.developer_progress.setRange(0, len(self._developer_images))
        self.developer_progress.setValue(0)
        self.developer_progress.setFormat(
            f"Loop {self._developer_loop_count}: 0 / {len(self._developer_images)}"
        )

        worker = DeveloperWorker(
            self._developer_images,
            self._developer_config,
            self.model_pool,
            delay_ms=self.spin_developer_delay.value(),
            parent=self,
        )
        worker.result_ready.connect(self._on_developer_result)
        worker.image_error.connect(self._on_developer_image_error)
        worker.phase_changed.connect(self._on_developer_phase)
        worker.batch_completed.connect(self._on_developer_batch_completed)
        worker.finished.connect(self._on_developer_thread_finished)
        self.developer_worker = worker
        worker.start()
        bus.log_message.emit(
            f"Developer loop {self._developer_loop_count} started "
            f"({len(self._developer_images)} images)",
            "INF",
        )

    def _on_developer_phase(self, phase: str, index: int, total: int):
        if phase == "DELAY":
            self.developer_progress.setFormat(
                f"Loop {self._developer_loop_count}: {index}/{total} - "
                f"delay {self.spin_developer_delay.value()} ms"
            )
            return
        self._set_developer_status(phase, "wait")
        self.developer_progress.setFormat(
            f"Loop {self._developer_loop_count}: {index}/{total} - {phase}"
        )

    def _on_developer_result(self, image_path: str, result: DetectionResult, index: int, total: int):
        if result.dst is not None:
            self._display_frame(result.dst, self.developer_canvas)
        elif result.src is not None:
            self._display_frame(result.src, self.developer_canvas)
        file_name = os.path.basename(image_path)
        self.label_developer_file.setText(file_name)
        self.label_developer_file.setToolTip(image_path)

        result_text = "OK" if result.ret == "PASS" else "NG"
        result_style = "pass" if result.ret == "PASS" else "fail"
        self._set_developer_status(result_text, result_style)
        detail = result.error or result.label_counts
        self._append_developer_row(index, file_name, result_text, detail, f"{result.timecheck}ms")
        self.developer_progress.setValue(index)
        self._update_developer_summary()

        if not self.hardware_controller.server.send_data_to_all_clients(result_text):
            bus.log_message.emit(
                f"Developer result '{result_text}' could not be returned to the TCP client", "ERR"
            )
        else:
            bus.log_message.emit(
                f"Developer loop {self._developer_loop_count} "
                f"[{index}/{total}] {file_name}: {result_text} sent to TCP client",
                "INF",
            )

    def _on_developer_image_error(self, image_path: str, error: str, index: int, total: int):
        file_name = os.path.basename(image_path)
        self.label_developer_file.setText(file_name)
        self.label_developer_file.setToolTip(image_path)
        self._set_developer_status("ERROR", "fail")
        self._append_developer_row(index, file_name, "ERROR", error, "-")
        self.developer_progress.setValue(index)
        self._update_developer_summary()
        self.hardware_controller.server.send_data_to_all_clients("NG")
        bus.log_message.emit(
            f"Developer loop {self._developer_loop_count} "
            f"[{index}/{total}] {file_name}: {error}; NG sent to TCP client",
            "ERR",
        )

    def _append_developer_row(self, index, file_name, result_text, detail, duration):
        self._make_room_for_log_row(self.developer_log_table)
        row = self.developer_log_table.rowCount()
        self.developer_log_table.insertRow(row)
        values = [str(index), file_name, result_text, detail or "-", duration]
        is_ok = result_text == "OK"
        row_color = QColor("#bbf7d0" if is_ok else "#fecaca")
        foreground = QColor("#14532d" if is_ok else "#7f1d1d")
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setBackground(row_color)
            item.setForeground(foreground)
            if column == 2:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.developer_log_table.setItem(row, column, item)
        self.developer_log_table.scrollToBottom()

    def _update_developer_summary(self):
        ok_count = ng_count = error_count = 0
        for row in range(self.developer_log_table.rowCount()):
            item = self.developer_log_table.item(row, 2)
            value = item.text() if item is not None else ""
            ok_count += value == "OK"
            ng_count += value == "NG"
            error_count += value == "ERROR"
        self.label_developer_summary.setText(
            f"OK: {ok_count}   NG: {ng_count}   ERROR: {error_count}"
        )

    def _clear_developer_results(self):
        self.developer_log_table.setRowCount(0)
        self.label_developer_summary.setText("OK: 0   NG: 0   ERROR: 0")

    def _set_developer_status(self, text: str, result_style: str):
        self.label_developer_status.setText(text)
        object_name = {
            "pass": "label_status_pass",
            "fail": "label_status_fail",
        }.get(result_style, "label_status_wait")
        self.label_developer_status.setObjectName(object_name)
        for widget in (
            self.developer_status_frame,
            self.developer_canvas_container,
            self.label_developer_status,
        ):
            widget.setProperty("resultStatus", result_style)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _on_toggle_developer_continuous(self, enabled: bool):
        self.btn_developer_continuous.setText(
            "Continuous: ON" if enabled else "Continuous: OFF"
        )
        state = "enabled" if enabled else "disabled"
        bus.log_message.emit(f"Developer continuous loop {state}", "INF")
        if not enabled:
            self._developer_restart_pending = False
            if self.is_developer_armed and not self.is_developer_running:
                self._set_developer_status("WAIT TEST", "wait")
                self.developer_progress.setFormat(
                    f"Waiting for '{self.line_developer_trigger.text().strip()}'"
                )

    def _on_developer_batch_completed(
        self, processed: int, ok_count: int, ng_count: int, error_count: int, cancelled: bool
    ):
        self.is_developer_running = False
        if cancelled:
            self._set_developer_status("STOPPED", "wait")
            self.developer_progress.setFormat(f"Stopped after {processed} images")
            bus.log_message.emit(
                f"Developer simulation stopped after {processed} images", "WRN"
            )
        else:
            final_style = "pass" if ng_count == 0 and error_count == 0 else "fail"
            self._set_developer_status("COMPLETE", final_style)
            self.developer_progress.setValue(len(self._developer_images))
            self.developer_progress.setFormat(
                f"Complete: {processed} | OK {ok_count} | NG {ng_count} | Error {error_count}"
            )
            bus.log_message.emit(
                f"Developer simulation complete: {processed} images, "
                f"OK={ok_count}, NG={ng_count}, ERROR={error_count}",
                "INF",
            )
        should_restart = (
            not cancelled
            and self.is_developer_armed
            and self.btn_developer_continuous.isChecked()
        )
        if should_restart:
            self._developer_restart_pending = True
            self._set_developer_status("RESTARTING", "wait")
            self.developer_progress.setFormat(
                f"Loop {self._developer_loop_count} complete - restarting from first image"
            )
            self.statusBar().showMessage(
                f"DEVELOPER: continuous loop {self._developer_loop_count} complete"
            )
        elif self.is_developer_armed:
            self.statusBar().showMessage(
                f"DEVELOPER: ready for next '{self.line_developer_trigger.text().strip()}' trigger"
            )

    def _on_developer_thread_finished(self):
        worker = self.sender()
        if worker is self.developer_worker:
            self.developer_worker = None
        if not self.is_developer_armed:
            self._finish_developer_disarm()
        elif self._developer_restart_pending and self.btn_developer_continuous.isChecked():
            self._developer_restart_pending = False
            QTimer.singleShot(0, self._start_developer_batch)
        else:
            self._developer_restart_pending = False

    def _set_developer_controls(self, armed: bool):
        self.btn_developer_arm.setEnabled(not armed)
        self.btn_developer_stop.setEnabled(armed)
        self.btn_developer_browse.setEnabled(not armed)
        self.line_developer_trigger.setEnabled(not armed)
        self.spin_developer_delay.setEnabled(not armed)
        self.chk_developer_recursive.setEnabled(not armed)
        self.btn_start_auto.setEnabled(not armed and not self.is_auto_running)
        self.btn_tcp_start.setEnabled(
            not armed and not self.hardware_controller.is_connect_server
        )
        self.btn_tcp_stop.setEnabled(
            not armed and self.hardware_controller.is_connect_server
        )

    def _on_stop_developer(self):
        self.is_developer_armed = False
        self._developer_restart_pending = False
        self._set_developer_controls(False)
        if self.developer_worker is not None and self.developer_worker.isRunning():
            self.btn_developer_arm.setEnabled(False)
            self._set_developer_status("STOPPING", "wait")
            self.developer_progress.setFormat("Stopping after the current inference...")
            self.developer_worker.request_stop()
            return
        self._finish_developer_disarm()

    def _finish_developer_disarm(self):
        self.is_developer_running = False
        self._developer_restart_pending = False
        if self._developer_opened_tcp:
            self.hardware_controller.close_server()
        self._developer_opened_tcp = False
        self._set_developer_controls(False)
        self._set_developer_status("DISARMED", "wait")
        self.statusBar().showMessage("Developer Mode: STOPPED")
        bus.log_message.emit("Developer mode disarmed", "INF")

    def _on_start_auto(self):
        if self.is_developer_armed:
            QMessageBox.warning(
                self, "Start Auto", "Stop Developer mode before starting Auto mode."
            )
            return
        if self.is_auto_running:
            bus.log_message.emit("Auto Inspection is already running.", "WRN")
            return
        config = self._config_from_ui()
        config.shapes = self._get_shapes_from_canvas()
        bypass_enabled = self.btn_bypass.isChecked()

        if not bypass_enabled and len(config.shapes) > 1:
            QMessageBox.warning(self, "Warning", "Please keep at most one ROI before starting Auto mode.")
            return
        if not bypass_enabled and not config.model_ai.model_name:
            QMessageBox.warning(self, "Warning", "Please select a YOLO model before starting Auto mode.")
            return
        if not bypass_enabled and not config.shapes:
            bus.log_message.emit("No ROI configured; Auto detection will use the full image.", "INF")
        # Auto owns camera capture timing; stop the competing preview reader.
        if not self._stop_camera_preview():
            QMessageBox.critical(
                self, "Start Auto",
                "Camera preview could not stop, so Auto mode was not started."
            )
            return

        camera_ok, camera_error = self._ensure_auto_camera_ready(config, bypass_enabled)
        if not camera_ok:
            bus.log_message.emit(f"Auto camera startup failed: {camera_error}", "ERR")
            QMessageBox.critical(self, "Start Auto", camera_error)
            return

        light_ok, light_error = self._ensure_auto_light_ready(config, bypass_enabled)
        if not light_ok:
            self._stop_auto_owned_camera()
            bus.log_message.emit(f"Auto light startup failed: {light_error}", "ERR")
            QMessageBox.critical(self, "Start Auto", light_error)
            return

        io_ok, io_error = self._start_selected_auto_io(config)
        if not io_ok:
            self._stop_auto_owned_light()
            self._stop_auto_owned_camera()
            bus.log_message.emit(f"Auto I/O startup failed: {io_error}", "ERR")
            QMessageBox.critical(self, "Start Auto", io_error)
            return

        self.current_config = config
        self._apply_image_retention_config(config)
        self.is_auto_running = True
        self._static_image_mode = False

        # Switch to Auto tab
        self.tab_widget.setCurrentWidget(self.tab_auto)

        # Start auto controller + worker
        try:
            self.auto_controller.set_bypass_mode(bypass_enabled)
            self.auto_controller.start(config)
            self.auto_worker = AutoWorker(self.auto_controller)
            self.auto_worker.start()
        except Exception as exc:
            self.is_auto_running = False
            self._on_auto_state_changed(False)
            self._stop_auto_owned_io()
            self._stop_auto_owned_light()
            self._stop_auto_owned_camera()
            bus.log_message.emit(f"Auto startup failed: {exc}", "ERR")
            QMessageBox.critical(self, "Start Auto", str(exc))

    def _on_toggle_bypass(self, enabled: bool):
        if enabled:
            answer = QMessageBox.question(
                self,
                "Enable BYPASS",
                "BYPASS captures and saves an image for every valid trigger, skips model inspection, and forces OK. Continue?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.btn_bypass.setChecked(False)
                return

        self.auto_controller.set_bypass_mode(enabled)
        self.btn_bypass.setText("BYPASS: ON - FORCE OK" if enabled else "BYPASS: OFF")
        self.btn_bypass.setProperty("bypassActive", enabled)
        self.btn_bypass.style().unpolish(self.btn_bypass)
        self.btn_bypass.style().polish(self.btn_bypass)
        self._set_bypass_indicator_active(enabled)
        if enabled:
            self.statusBar().showMessage("BYPASS enabled", 3000)
        elif not self.is_auto_running:
            self.statusBar().showMessage("BYPASS disabled", 3000)

    def _set_bypass_indicator_active(self, enabled: bool):
        self._bypass_blink_timer.stop()
        self.label_bypass_indicator.setVisible(enabled)
        if enabled:
            self.label_bypass_indicator.raise_()
            self._bypass_blink_timer.start()

    def _blink_bypass_indicator(self):
        self.label_bypass_indicator.setVisible(
            not self.label_bypass_indicator.isVisible()
        )
        if self.label_bypass_indicator.isVisible():
            self.label_bypass_indicator.raise_()

    def _on_stop_auto(self):
        stopped = True
        if self.auto_worker:
            stopped = self.auto_worker.stop()
            if stopped:
                self.auto_worker = None
        self.is_auto_running = False
        if not stopped:
            bus.log_message.emit("Auto worker did not stop within 5 seconds.", "ERR")
            self.is_auto_running = True
            self.btn_start_auto.setEnabled(False)
            self.btn_stop_auto.setEnabled(False)
            self.statusBar().showMessage("Auto Inspection Mode: STOPPING")
            QMessageBox.warning(
                self, "Stop Auto",
                "The current inspection is still finishing. The UI will unlock when it stops safely."
            )
            QTimer.singleShot(250, self._finish_auto_stop_when_ready)
        else:
            self._stop_auto_owned_io()
            self._stop_auto_owned_light()
            self._stop_auto_owned_camera()

    def _finish_auto_stop_when_ready(self):
        if self.auto_worker is not None and self.auto_worker.isRunning():
            QTimer.singleShot(250, self._finish_auto_stop_when_ready)
            return
        self.auto_worker = None
        self._on_auto_state_changed(False)
        self._stop_auto_owned_io()
        self._stop_auto_owned_light()
        self._stop_auto_owned_camera()

    def _on_auto_state_changed(self, is_running: bool):
        self.is_auto_running = is_running
        if is_running:
            self.tab_widget.setCurrentWidget(self.tab_auto)
        for tab in (self.tab_developer, self.tab_teaching, self.tab_io):
            self.tab_widget.setTabEnabled(self.tab_widget.indexOf(tab), not is_running)
        self.combo_model.setEnabled(not is_running)
        self.btn_load_config.setEnabled(not is_running)
        self.btn_save_config.setEnabled(not is_running)
        self.btn_new_config.setEnabled(not is_running)
        self.shortcut_save_config.setEnabled(not is_running)
        self.btn_bypass.setEnabled(not is_running)
        self.btn_start_auto.setEnabled(not is_running and not self.is_developer_armed)
        self.btn_stop_auto.setEnabled(is_running)
        self.btn_capture.setEnabled(not is_running)
        self.btn_live_camera.setEnabled(not is_running and self.hardware_controller.is_open_camera)
        self.btn_draw_roi.setEnabled(not is_running)
        self.btn_clear_roi.setEnabled(not is_running)
        self.btn_load_image.setEnabled(not is_running)
        self.btn_select_crop_folder.setEnabled(not is_running)
        self.btn_crop_folder.setEnabled(not is_running)
        self.btn_open_cam.setEnabled(not is_running and not self.hardware_controller.is_open_camera)
        self.btn_close_cam.setEnabled(not is_running and self.hardware_controller.is_open_camera)
        if is_running:
            if (self.hardware_controller.is_open_light and
                    self.hardware_controller.is_light_on and
                    not self.hardware_controller.set_light_values(0, 0, 0, 0, 0)):
                bus.log_message.emit("Could not switch manual light off before Auto mode", "ERR")
            self._set_light_button_state(False, "LIGHT: AUTO")
            self.btn_toggle_light.setEnabled(False)
        else:
            self._set_light_button_state(False)
            self.btn_toggle_light.setEnabled(self.hardware_controller.is_open_light)
        if is_running:
            self.btn_tcp_start.setEnabled(False)
            self.btn_tcp_stop.setEnabled(False)
            self.btn_com_open.setEnabled(False)
            self.btn_com_close.setEnabled(False)
            self.combo_plc_com.setEnabled(False)
            self.combo_plc_baud.setEnabled(False)
            self.btn_refresh_plc_com.setEnabled(False)
        else:
            self.btn_tcp_start.setEnabled(not self.hardware_controller.is_connect_server)
            self.btn_tcp_stop.setEnabled(self.hardware_controller.is_connect_server)
            self.btn_com_open.setEnabled(not self.hardware_controller.is_com_send_data)
            self.btn_com_close.setEnabled(self.hardware_controller.is_com_send_data)
            com_settings_enabled = not self.hardware_controller.is_com_send_data
            self.combo_plc_com.setEnabled(com_settings_enabled)
            self.combo_plc_baud.setEnabled(com_settings_enabled)
            self.btn_refresh_plc_com.setEnabled(com_settings_enabled)
        self.btn_start_auto.setProperty("activeAction", not is_running)
        self.btn_stop_auto.setProperty("activeAction", is_running)
        for button in (self.btn_start_auto, self.btn_stop_auto):
            button.style().unpolish(button)
            button.style().polish(button)
        if is_running:
            self.statusBar().showMessage("AUTO: waiting for TCP/COM trigger")
        else:
            self.statusBar().showMessage("Auto Inspection Mode: STOPPED")

    # ================================================================
    # SIGNAL HANDLERS
    # ================================================================
    def _on_show_image(self, image):
        if image is not None:
            self._display_frame(image, self.auto_canvas)

    def _on_show_status(self, status: str):
        status_upper = status.upper()
        if status_upper == "PASS":
            self.label_big_status.setText("OK")
            self.label_big_status.setObjectName("label_status_pass")
            result_style = "pass"
            self.count_ok += 1
        elif status_upper in ("FAIL",):
            self.label_big_status.setText("NG")
            self.label_big_status.setObjectName("label_status_fail")
            result_style = "fail"
            self.count_ng += 1
        else:
            self.label_big_status.setText("WAIT")
            self.label_big_status.setObjectName("label_status_wait")
            result_style = "wait"

        # Color both the status card and image viewport border for instant recognition.
        for widget in (
            self.status_frame,
            self.auto_canvas_container,
            self.label_big_status,
        ):
            widget.setProperty("resultStatus", result_style)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

        if status_upper in ("PASS", "FAIL"):
            self.count_total += 1
            self.label_ok_count.setText(str(self.count_ok))
            self.label_ng_count.setText(str(self.count_ng))
            self.label_total_count.setText(str(self.count_total))

    def _on_hardware_status(self, device_name: str, connected: bool):
        # Map device names
        display_map = {
            "Camera": "Camera",
            "Server": "TCP",
            "COM": "COM",
            "Light": "Light",
        }
        display_name = display_map.get(device_name, device_name)
        if display_name in self.hw_indicators:
            lbl, icon_on, icon_off = self.hw_indicators[display_name]
            icon = icon_on if connected else icon_off
            status_name = "Camera READY" if display_name == "Camera" and connected else display_name
            lbl.setText(f"{icon} {status_name}")

    def _on_log_message(self, message: str, level: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f'<span style="color:#111827">[{timestamp}] [{level}] {message}</span>'
        for log_widget in (self.text_teaching_log, self.text_sys_log):
            self._append_system_log_line(log_widget, line)

    @staticmethod
    def _append_system_log_line(log_widget: QTextEdit, line: str) -> bool:
        """Keep the first 200 UI log lines and discard newer overflow lines.

        This affects only the in-memory QTextEdit. Persistent JSON logging is
        handled independently by ``data.logger.JsonArrayFileHandler``.
        """
        has_content = bool(log_widget.toPlainText())
        if has_content and log_widget.document().blockCount() >= SYSTEM_LOG_LIMIT:
            return False
        log_widget.append(line)
        return True

    def _on_write_log(self, result: DetectionResult):
        # Add to log table
        self._make_room_for_log_row(self.log_table)
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_table.setItem(row, 0, QTableWidgetItem(timestamp))

        result_text = "OK" if result.ret == "PASS" else "NG"
        row_color = QColor("#bbf7d0" if result.ret == "PASS" else "#fecaca")
        row_items = [
            self.log_table.item(row, 0),
            QTableWidgetItem(result_text),
            QTableWidgetItem(result.label_counts),
            QTableWidgetItem(f"{result.timecheck}ms"),
        ]
        result_font = row_items[1].font()
        result_font.setBold(True)
        row_items[1].setFont(result_font)
        for column, item in enumerate(row_items):
            item.setForeground(QColor("#14532d" if result.ret == "PASS" else "#7f1d1d"))
            item.setBackground(row_color)
            self.log_table.setItem(row, column, item)

        # Scroll to bottom
        self.log_table.scrollToBottom()

        # Save to database
        try:
            self.db_manager.add_entry(
                self.db_table,
                model_name=result.model_name,
                result=result.ret,
                label_counts=result.label_counts,
                timecheck=str(result.timecheck),
            )
        except Exception as e:
            bus.log_message.emit(f"DB write failed: {e}", "ERR")

        # Save image
        try:
            ret_label = "OK" if result.ret == "PASS" else "NG"
            log_dir = "Log_Vision/LogImage"
            log_size = 100.0 * 1024.0
            if self.current_config is not None:
                log_dir = self.current_config.hardware.system.log_dir
                log_size = self.current_config.hardware.system.log_size
            saved_path = self.main_logger.log_image(
                result.model_name,
                result.dst if result.dst is not None else result.src,
                log_dir,
                ret=ret_label,
                max_size_mb=log_size,
            )
            if not saved_path and not self.main_logger.is_image_saving_suspended(log_dir):
                bus.log_message.emit("Inspection image could not be saved.", "ERR")
        except Exception as e:
            bus.log_message.emit(f"Image log failed: {e}", "ERR")

    @staticmethod
    def _make_room_for_log_row(table: QTableWidget):
        """Keep only the newest UI records without affecting persistent logs."""
        while table.rowCount() >= INSPECTION_LOG_LIMIT:
            table.removeRow(0)

    def _on_reset_counter(self):
        answer = QMessageBox.question(
            self, "Reset Counter", "Reset OK, NG and Total counters?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.count_ok = 0
        self.count_ng = 0
        self.count_total = 0
        self.label_ok_count.setText("0")
        self.label_ng_count.setText("0")
        self.label_total_count.setText("0")
        bus.log_message.emit("Inspection counters reset.", "INF")

    # ================================================================
    # CLEANUP
    # ================================================================
    def closeEvent(self, event):
        answer = QMessageBox.question(
            self,
            "Save Configuration",
            "Save the current configuration before exiting?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if answer == QMessageBox.Cancel:
            event.ignore()
            return
        if answer == QMessageBox.Save and not self._save_config(
            confirm_overwrite=False,
            show_success=False,
        ):
            event.ignore()
            return

        if self.auto_worker:
            if not self.auto_worker.stop():
                QMessageBox.critical(self, "Exit", "Auto worker is still busy. Stop Auto and try again.")
                event.ignore()
                return
        if self.developer_worker is not None and self.developer_worker.isRunning():
            self.is_developer_armed = False
            if not self.developer_worker.stop():
                QMessageBox.critical(
                    self,
                    "Exit",
                    "Developer simulation is still processing. Stop it and try again.",
                )
                event.ignore()
                return
        if self._developer_opened_tcp:
            self.hardware_controller.close_server()
            self._developer_opened_tcp = False
        if not self._stop_camera_preview():
            QMessageBox.critical(self, "Exit", "Camera preview is still busy. Disconnect it and try again.")
            event.ignore()
            return
        if not self.image_retention.stop(timeout=3.0):
            bus.log_message.emit("Image retention worker did not stop within 3 seconds.", "WRN")
        self.hardware_controller.cleanup_all()
        self.db_manager.close()
        event.accept()
