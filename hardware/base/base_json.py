from abc import ABC, abstractmethod

class HandleJSON(ABC):
    @abstractmethod
    def add(self, *args, **kwargs):
        pass

    @abstractmethod
    def delete(self, *args, **kwargs):
        pass

    @abstractmethod
    def save(self, *args, **kwargs):
        pass

    @abstractmethod
    def load(self, *args, **kwargs):
        pass
    
    @abstractmethod
    def update_config_key(self, *args, **kwargs):
        pass
