import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4

import cv2

from .models import CodeSummary, Status


def read_image(path):
    import numpy as np
    frame = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise IOError(f"Không đọc được ảnh: {path}")
    return frame


def annotate(image, result):
    output = image.copy()
    for x1, y1, x2, y2, label, confidence in result.boxes:
        color = (0, 190, 0) if label == "target" else (0, 0, 255) if label == "foreign" else (0, 190, 255)
        cv2.rectangle(output, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        cv2.putText(output, f"{label} {confidence:.2f}", (int(x1), max(16, int(y1) - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return output


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(self.root / "tray_log.db"), check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY, work_date TEXT NOT NULL, code TEXT NOT NULL,
                qty_expected INTEGER NOT NULL CHECK(qty_expected>0), status TEXT NOT NULL,
                qty_counted INTEGER NOT NULL DEFAULT 0, finished_at TEXT,
                pending_cycle INTEGER NOT NULL DEFAULT 0, pending_replace_id INTEGER,
                UNIQUE(work_date, code));
            CREATE TABLE IF NOT EXISTS trays (
                id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
                seq INTEGER NOT NULL, captured_at TEXT NOT NULL, count INTEGER NOT NULL,
                n_foreign INTEGER NOT NULL, n_lowconf INTEGER NOT NULL, result INTEGER NOT NULL,
                model_version TEXT, image_path TEXT, annotated_path TEXT, infer_ms REAL,
                err_code INTEGER NOT NULL DEFAULT 0, replaced_by INTEGER REFERENCES trays(id));
            CREATE INDEX IF NOT EXISTS trays_order ON trays(order_id, replaced_by);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts TEXT NOT NULL, level TEXT NOT NULL,
                source TEXT NOT NULL, message TEXT NOT NULL, actor TEXT NOT NULL);
        """)
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(orders)")}
        for name, definition in (("pending_cycle", "INTEGER NOT NULL DEFAULT 0"), ("pending_replace_id", "INTEGER")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def event(self, source, message, actor="system", level="INFO", db=None):
        values = (datetime.now().isoformat(timespec="milliseconds"), level, source, message, actor)
        if db is not None:
            db.execute("INSERT INTO events(ts,level,source,message,actor) VALUES(?,?,?,?,?)", values)
        else:
            with self.transaction() as conn:
                conn.execute("INSERT INTO events(ts,level,source,message,actor) VALUES(?,?,?,?,?)", values)

    def orders(self, work_date):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM orders WHERE work_date=? ORDER BY id", (str(work_date),))]

    def summary(self, order_id, db=None):
        with self.lock:
            conn = db or self.db
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            totals = conn.execute("""SELECT COALESCE(SUM(count),0) qty, COALESCE(SUM(n_foreign),0) foreign_qty,
                COALESCE(SUM(n_lowconf),0) low_qty, COALESCE(SUM(result=3),0) errors, COUNT(*) n
                FROM trays WHERE order_id=? AND replaced_by IS NULL""", (order_id,)).fetchone()
            return CodeSummary(row["code"], row["qty_expected"], totals["qty"], totals["foreign_qty"],
                               totals["low_qty"], totals["errors"], totals["n"], row["status"])

    def last_tray(self, order_id, db=None):
        with self.lock:
            row = (db or self.db).execute("SELECT * FROM trays WHERE order_id=? AND replaced_by IS NULL ORDER BY id DESC LIMIT 1", (order_id,)).fetchone()
            return dict(row) if row else None

    def trays(self, order_id):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM trays WHERE order_id=? ORDER BY id DESC", (order_id,))]

    @staticmethod
    def _write_png(path, frame):
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise IOError(f"Không mã hóa được ảnh: {path}")
        encoded.tofile(str(path))

    def save_tray(self, order_id, result, image, actor, replace_id=None, error=0):
        created = []
        try:
            with self.transaction() as db:
                order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
                if order is None or order["status"] != Status.COUNTING.value:
                    raise ValueError("Code không ở trạng thái Đang đếm")
                previous = self.last_tray(order_id, db)
                if replace_id is not None and (not previous or previous["id"] != replace_id):
                    raise ValueError("Chỉ được thay kết quả tray vừa rồi")
                seq = previous["seq"] if replace_id else db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM trays WHERE order_id=?", (order_id,)).fetchone()[0]
                now = datetime.now()
                image_path = annotated_path = ""
                plc_result = 3 if error else 1 if result.ok_tray else 2
                if image is not None:
                    # Code is stored verbatim in SQLite; the directory component is sanitized.
                    safe_code = re.sub(r"[^\w.-]", "_", order["code"]).strip(" .")[:100] or "code"
                    folder = self.root / "images" / order["work_date"].replace("-", "") / safe_code
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / f"{seq:04d}_{now:%H%M%S_%f}_{uuid4().hex[:8]}.png"
                    created.append(path)
                    self._write_png(path, image)
                    image_path = str(path)
                    if plc_result != 1:
                        annotated = path.with_stem(path.stem + "_NG")
                        created.append(annotated)
                        self._write_png(annotated, annotate(image, result))
                        annotated_path = str(annotated)
                cursor = db.execute("""INSERT INTO trays(order_id,seq,captured_at,count,n_foreign,n_lowconf,result,
                    model_version,image_path,annotated_path,infer_ms,err_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (order_id, seq, now.isoformat(timespec="milliseconds"), result.count, result.n_foreign,
                     result.n_lowconf, plc_result, result.model_version, image_path, annotated_path, result.infer_ms, int(error)))
                tray_id = cursor.lastrowid
                if replace_id:
                    db.execute("UPDATE trays SET replaced_by=? WHERE id=?", (tray_id, replace_id))
                qty = self.summary(order_id, db).qty_counted
                db.execute("UPDATE orders SET qty_counted=?,pending_cycle=0,pending_replace_id=NULL WHERE id=?", (qty, order_id))
                self.event("user" if replace_id else "worker", json.dumps({"code": order["code"], "tray_id": tray_id,
                           "replaces": replace_id, "result": plc_result}, ensure_ascii=False), actor, db=db)
                return tray_id
        except Exception:
            for path in created:
                path.unlink(missing_ok=True)
            raise

    def export_report(self, work_date, path=None):
        from openpyxl import Workbook
        day = date.fromisoformat(str(work_date)).isoformat()
        path = Path(path) if path else self.root / "reports" / f"tray_{day}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        workbook.remove(workbook.active)
        queries = {
            "Orders": ("SELECT * FROM orders WHERE work_date=? ORDER BY id", (day,)),
            "Trays": ("SELECT t.* FROM trays t JOIN orders o ON t.order_id=o.id WHERE o.work_date=? ORDER BY t.id", (day,)),
            "Events": ("SELECT * FROM events WHERE substr(ts,1,10)=? ORDER BY id", (day,)),
        }
        with self.lock:
            for name, (query, args) in queries.items():
                sheet = workbook.create_sheet(name)
                cursor = self.db.execute(query, args)
                sheet.append([col[0] for col in cursor.description])
                for row in cursor:
                    sheet.append(list(row))
                    # Treat production codes and user input as text, never Excel formulas.
                    for cell in sheet[sheet.max_row]:
                        if isinstance(cell.value, str):
                            cell.data_type = "s"
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                for col in sheet.columns:
                    sheet.column_dimensions[col[0].column_letter].width = min(55, max(14, max(len(str(c.value or "")) for c in col) + 2))
        workbook.save(path)
        return str(path.resolve())

    def cleanup_images(self, keep_days, today=None):
        if keep_days < 1:
            raise ValueError("keep_days phải >= 1")
        root = (self.root / "images").resolve()
        original = self.root / "images"
        if root.parent != self.root or original.is_symlink() or getattr(original, "is_junction", lambda: False)():
            raise ValueError("Thư mục ảnh không được chuyển hướng ra ngoài thư mục dữ liệu")
        cutoff = (today or date.today()) - timedelta(days=keep_days)
        if root.is_dir():
            for folder in root.iterdir():
                if not re.fullmatch(r"\d{8}", folder.name) or not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    day = datetime.strptime(folder.name, "%Y%m%d").date()
                except ValueError:
                    continue
                resolved = folder.resolve()
                if day < cutoff and resolved.parent == root and not getattr(folder, "is_junction", lambda: False)():
                    shutil.rmtree(resolved)

    def close(self):
        with self.lock:
            self.db.close()
