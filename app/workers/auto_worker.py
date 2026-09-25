from PyQt5.QtCore import QThread
from app.signals import bus

class AutoWorker(QThread):
    
    def __init__(self, auto_controller):
        super().__init__()
        self.controller = auto_controller
        self._running = False

    def run(self):
        self._running = True
        while self._running and self.controller.is_running:
            try:
                self.controller.run_step()
            except Exception as e:
                bus.log_message.emit(f"Auto worker error: {e}", "ERR")
            self.msleep(10)

    def stop(self):
        self._running = False
        self.controller.stop()
        return self.wait(5000)
