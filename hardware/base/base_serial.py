from abc import ABCMeta, abstractmethod
from PyQt5.QtCore import QObject, pyqtSignal

class MetaQObjectABC(ABCMeta, type(QObject)):
    pass

class AbstractSerialReceiver(QObject, metaclass=MetaQObjectABC):
    checkSignalScanner = pyqtSignal(str)

    def __init__(self):
        super().__init__()

    @abstractmethod
    def connect(self, port, baudrate):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def read_data(self):
        pass

    @abstractmethod
    def is_connected(self):
        pass
    
    @abstractmethod
    def send_data(self, data):
        pass
