from abc import ABC, abstractmethod

class AbstractDatabaseManager(ABC):
    @abstractmethod
    def create_table(self, table_name, **columns):
        pass

    @abstractmethod
    def add_entry(self, table, **kwargs):
        pass

    @abstractmethod
    def get_all(self, table):
        pass

    @abstractmethod
    def get_by_id(self, table, entry_id):
        pass

    @abstractmethod
    def update_entry(self, table, entry_id, **kwargs):
        pass

    @abstractmethod
    def delete_entry(self, table, entry_id):
        pass

    @abstractmethod
    def filter_entries(self, table, **filters):
        pass

    @abstractmethod
    def sort_entries(self, table, column, descending=False):
        pass

    @abstractmethod
    def close(self):
        pass
