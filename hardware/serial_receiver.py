import serial
import threading
import time
from PyQt5.QtCore import pyqtSignal
from data.logger import Logger
from hardware.base.base_serial import AbstractSerialReceiver

class SerialReceiver(AbstractSerialReceiver):
    """Encapsulates serial port connection and background trigger reading loop."""
    checkSignalScanner = pyqtSignal(str)
    readData = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.ser = None
        self.scanner_logger = Logger('Scanner')
        self.connected = False

    def connect(self, port, baudrate):
        try:
            self.ser = serial.Serial(port, baudrate, timeout=1)
            self.scanner_logger.info(f'Connected to {port} at {baudrate}')
            self.connected = True
            threading.Thread(target=self.read_data, daemon=True).start()
            return True
        except serial.SerialException as e:
            self.scanner_logger.error(f"Failed to connect COM: {e}")
            self.connected = False
            return False

    def disconnect(self):
        self.connected = False
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
                self.scanner_logger.info('Disconnected')
            except Exception as e:
                self.scanner_logger.error(f"Error closing COM: {e}")

    def read_data(self):
        self.scanner_logger.info('Start wait trigger ...')
        try:
            while self.connected:
                if not self.ser or not self.ser.is_open:
                    break
                
                if self.ser.in_waiting > 0:
                    line = self.ser.readline()
                    if not line:
                        continue

                    code = line.decode('utf-8').strip()
                    if code:
                        self.scanner_logger.info(f"Received: {code}")
                        self.readData.emit(code)
                else:
                    time.sleep(0.01)  # prevent 100% CPU usage
        except Exception as e:
            if self.connected:
                self.scanner_logger.error(f"Read error: {e}")

    def is_connected(self):
        return self.connected and self.ser and self.ser.is_open

    def send_data(self, data):
        """Sends data string (appended with newline character) over the serial interface."""
        if self.ser and self.ser.is_open:
            try:
                self.ser.write((data + '\n').encode('utf-8'))
                self.scanner_logger.info(f'Sent: {data}')
                return True
            except serial.SerialException as e:
                self.scanner_logger.error(f'Send error: {e}')
        else:
            self.scanner_logger.warning("Serial port not open")
        return False
