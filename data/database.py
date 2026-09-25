from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import pytz
from hardware.base.base_database import AbstractDatabaseManager

VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

class DatabaseManager(AbstractDatabaseManager):
    """Database utility class to dynamically generate tables and handle entries using SQLAlchemy."""
    
    def __init__(self, db_name="database.db"):
        self.engine = create_engine(f"sqlite:///{db_name}", echo=False)
        self.Base = declarative_base()
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.tables = {}

    def create_table(self, table_name, **columns):
        if table_name in self.tables:
            return self.tables[table_name]
        def vn_now():
            return datetime.now(VN_TZ)
        attrs = {
            "__tablename__": table_name,
            "id": Column(Integer, primary_key=True, autoincrement=True)
        }
        for col_name, col_type in columns.items():
            attrs[col_name] = Column(col_type, nullable=False)
        attrs["Time"] = Column(DateTime, default=vn_now)
        table_class = type(table_name, (self.Base,), attrs)
        self.tables[table_name] = table_class
        self.Base.metadata.create_all(self.engine)
        return table_class

    def add_entry(self, table, **kwargs):
        new_entry = table(**kwargs)
        self.session.add(new_entry)
        self.session.commit()
        return new_entry

    def get_all(self, table):
        return self.session.query(table).all()

    def get_by_id(self, table, entry_id):
        return self.session.query(table).filter_by(id=entry_id).first()

    def update_entry(self, table, entry_id, **kwargs):
        entry = self.get_by_id(table, entry_id)
        if entry:
            for key, value in kwargs.items():
                setattr(entry, key, value)
            self.session.commit()
            return entry
        return None

    def delete_entry(self, table, entry_id):
        entry = self.get_by_id(table, entry_id)
        if entry:
            self.session.delete(entry)
            self.session.commit()
            all_entries = self.get_all(table)
            for index, row in enumerate(all_entries, start=1):
                row.id = index
            self.session.commit()
            return True
        return False

    def filter_entries(self, table, **filters):
        query = self.session.query(table)
        for key, value in filters.items():
            query = query.filter(getattr(table, key) == value)
        return query.all()

    def sort_entries(self, table, column, descending=False):
        query = self.session.query(table)
        order_by_column = getattr(table, column)
        return query.order_by(order_by_column.desc() if descending else order_by_column.asc()).all()

    def close(self):
        self.session.close()
