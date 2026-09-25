from PyQt5.QtCore import QObject, pyqtSignal

class EventBus(QObject):
    # Display / UI update signals
    show_image = pyqtSignal(object)            # Emits np.ndarray
    show_status = pyqtSignal(str)              # Emits "PASS", "FAIL", "WAIT"
    
    # Hardware events
    trigger_received = pyqtSignal(str)         # Emits trigger string from TCP or serial
    inspection_triggered = pyqtSignal()        # Emits after a valid inspection trigger is accepted
    hardware_status = pyqtSignal(str, bool)    # Emits (device_name, is_connected)
    
    # Logging signals
    log_message = pyqtSignal(str, str)         # Emits (message, level)
    write_log = pyqtSignal(object)             # Emits DetectionResult
    
    # Auto loop status
    auto_started = pyqtSignal(bool)            # True when auto starts, False when stops

# Global EventBus instance
bus = EventBus()
