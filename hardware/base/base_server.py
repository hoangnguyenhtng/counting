from abc import ABCMeta, abstractmethod
from PyQt5.QtCore import QObject, pyqtSignal
import socket

class MetaQObjectABC(ABCMeta, type(QObject)):
    pass

class AbstractServer(QObject, metaclass=MetaQObjectABC):
    triggerOn = pyqtSignal(str)
    sendData = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.client_address = None
        self.client_socket = None
        self.server_socket = None
        self.is_connected = False
        self.ip = None
        self.port = None

    @abstractmethod
    def start_server(self, ip='127.0.0.1', port=8000):
        pass

    @abstractmethod
    def stop_server(self):
        pass

    @abstractmethod
    def accept_client(self):
        pass

    @abstractmethod
    def loop_recv_client(self, conn: socket.socket):
        pass
