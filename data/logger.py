import logging
import colorlog
import os
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal
import csv
import json
import threading
import cv2
import numpy as np

from app.paths import get_abs_path


class JsonArrayFileHandler(logging.Handler):
    """Persist log records as one valid JSON array per calendar day."""

    _write_lock = threading.RLock()

    def __init__(self, filename):
        super().__init__(logging.DEBUG)
        self.filename = os.path.abspath(filename)

    def emit(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "logger": record.name,
            "file": record.filename,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        try:
            with self._write_lock:
                folder = os.path.dirname(self.filename)
                os.makedirs(folder, exist_ok=True)
                encoded_entry = json.dumps(entry, ensure_ascii=False).encode("utf-8")

                if not os.path.exists(self.filename) or os.path.getsize(self.filename) == 0:
                    with open(self.filename, "wb") as target:
                        target.write(b"[\n  " + encoded_entry + b"\n]\n")
                    return

                # Replace only the array's closing bracket so appending remains O(1)
                # while the file is still valid JSON after every completed write.
                with open(self.filename, "r+b") as target:
                    target.seek(0, os.SEEK_END)
                    position = target.tell() - 1
                    while position >= 0:
                        target.seek(position)
                        if target.read(1) not in b" \t\r\n":
                            break
                        position -= 1
                    if position < 0:
                        raise ValueError(f"Invalid JSON log file: {self.filename}")
                    target.seek(position)
                    if target.read(1) != b"]":
                        raise ValueError(f"Invalid JSON log file: {self.filename}")
                    closing_position = position
                    position -= 1
                    while position >= 0:
                        target.seek(position)
                        if target.read(1) not in b" \t\r\n":
                            break
                        position -= 1
                    target.seek(position)
                    separator = b"\n  " if target.read(1) == b"[" else b",\n  "
                    target.seek(closing_position)
                    target.truncate()
                    target.write(separator + encoded_entry + b"\n]\n")
        except Exception:
            self.handleError(record)

class Logger(QObject):
    signalLog = pyqtSignal(str)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self.__logger = logging.getLogger(name)
        self.__logger.setLevel(logging.DEBUG)
        self._image_retention = None

        # All vision logs live below the portable application data directory.
        self.log_root = get_abs_path("Log_Vision")
        os.makedirs(self.log_root, exist_ok=True)

        self.current_date = datetime.now().strftime('%Y_%m_%d')
        self.__create_log_handlers()

    def __create_log_handlers(self):
        """Creates handlers for stdout and the daily JSON log file."""
        today = datetime.now().strftime('%Y_%m_%d')
        log_path = os.path.join(self.log_root, f'{today}.json')

        self.__formatter_color = colorlog.ColoredFormatter(
            fmt='%(log_color)s %(asctime)s %(name)s - %(filename)s - %(levelname)s - %(message)s',
            datefmt='%Y/%m/%d %H:%M:%S',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red'
            }
        )

        self.__formatter_no_color = logging.Formatter(
            fmt='%(asctime)s %(name)s - %(filename)s - %(levelname)s - %(message)s',
            datefmt='%Y/%m/%d %H:%M:%S'
        )

        self.__logger.handlers.clear()

        # File Handler
        try:
            log_handle = JsonArrayFileHandler(log_path)
            self.__logger.addHandler(log_handle)
        except Exception as e:
            print(f"Failed to create file log handler: {e}")

        # Stream Handler (console output)
        stream_handle = logging.StreamHandler()
        stream_handle.setLevel(logging.DEBUG)
        stream_handle.setFormatter(self.__formatter_color)
        self.__logger.addHandler(stream_handle)

    def __check_date_and_update_log(self):
        """Checks if the date has changed and rolls over the log file."""
        new_date = datetime.now().strftime('%Y_%m_%d')
        if new_date != self.current_date:
            self.current_date = new_date
            self.__create_log_handlers()

    def __emit_formatted_log(self, level, message):
        """Formats and emits the log record via PyQt signal."""
        self.__check_date_and_update_log()
        record = logging.LogRecord(
            name=self.__logger.name,
            level=level,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=None,
            exc_info=None
        )
        formatted_message = self.__formatter_no_color.format(record)
        self.signalLog.emit(formatted_message)

    def set_image_retention(self, image_retention):
        """Attach the background retention service used for inspection images."""
        self._image_retention = image_retention

    def is_image_saving_suspended(self, image_folder) -> bool:
        retention = self._image_retention
        if retention is None:
            return False
        image_folder = get_abs_path(image_folder)
        return retention.manages(image_folder) and retention.saving_suspended
    
    def log_image(self, model_name, image, image_folder, ret='PASS', max_size_mb=102400, log_csv=False):
        """Save an inspection image; retention is enforced by the background service.

        ``max_size_mb`` remains in the signature for compatibility with older
        callers. The configured ImageRetentionService is authoritative.
        """
        self.__check_date_and_update_log()
        logged_at = datetime.now()
        date_str = logged_at.strftime('%Y_%m_%d')
        time_str = logged_at.strftime('%H_%M_%S')
        result_label = "OK" if str(ret).strip().upper() in ("OK", "PASS") else "NG"

        image_folder = get_abs_path(image_folder)
        retention = self._image_retention
        if retention is not None and not retention.can_save_image(image_folder):
            return ""

        result_image_folder = os.path.join(image_folder, date_str, result_label)
        os.makedirs(result_image_folder, exist_ok=True)

        base_filename = f'{result_label}_{date_str}_{time_str}.jpg'
        image_path = os.path.join(result_image_folder, base_filename)
        image_path = self._get_unique_filename(image_path)
        
        try:
            if image is None:
                raise ValueError("Invalid image (None). Cannot save")
                
            success = cv2.imwrite(image_path, image)
            if not success:
                raise IOError("Error saving image")

            if retention is not None:
                retention.record_saved_image(image_path)

            image_path_fixed = image_path.replace("\\", "/")
            log_message = f'Image logged at {image_path_fixed}'
            self.info(log_message)

            if log_csv:
                csv_folder = os.path.join(self.log_root, 'CSV')
                os.makedirs(csv_folder, exist_ok=True)
                csv_path = os.path.join(csv_folder, f'{date_str}.csv')
                csv_exists = os.path.isfile(csv_path)

                with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    if not csv_exists:
                        writer.writerow(['Model', 'Result', 'Time'])
                    writer.writerow([model_name, result_label, time_str])

            return image_path_fixed
        except Exception as ex:
            self.error(f"Error in log_image: {ex}")
            return ""

    def _get_unique_filename(self, filepath):
        if not os.path.exists(filepath):
            return filepath
        
        directory = os.path.dirname(filepath)
        filename = os.path.basename(filepath)
        name, ext = os.path.splitext(filename)
        
        counter = 1
        while True:
            new_filename = f"{name}({counter}){ext}"
            new_filepath = os.path.join(directory, new_filename)
            if not os.path.exists(new_filepath):
                return new_filepath
            counter += 1

    def debug(self, message):
        self.__emit_formatted_log(logging.DEBUG, str(message))
        self.__logger.debug(message, stacklevel=2)

    def info(self, message):
        self.__emit_formatted_log(logging.INFO, str(message))
        self.__logger.info(message, stacklevel=2)

    def warning(self, message, exc=None):
        self.__emit_formatted_log(logging.WARNING, str(message))
        self.__logger.warning(message, stacklevel=2, exc_info=exc)

    def error(self, message, exc=None):
        self.__emit_formatted_log(logging.ERROR, str(message))
        self.__logger.error(message, stacklevel=2, exc_info=exc)

    def critical(self, message, exc=None):
        self.__emit_formatted_log(logging.CRITICAL, str(message))
        self.__logger.critical(message, stacklevel=2, exc_info=exc)
