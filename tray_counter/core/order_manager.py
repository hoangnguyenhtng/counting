import hmac
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .models import ConfirmationRequired, ErrorCode, Result, Status


class OrderManager:
    """All mutations are serialized by Worker; persistence is transactional."""
    def __init__(self, registry, storage, code_column="code", qty_column="qty", duplicates="error", work_date=None):
        if duplicates not in ("error", "merge"):
            raise ValueError("duplicates phải là error hoặc merge")
        self.registry, self.storage = registry, storage
        self.code_column, self.qty_column, self.duplicates = code_column, qty_column, duplicates
        self.work_date = str(work_date or date.today())
        self.current_id = None
        self.actor = ""
        self.recount_id = None

    @staticmethod
    def _actor(actor):
        actor = str(actor).strip()
        if not actor:
            raise ValueError("Cần nhập tên người thao tác")
        return actor

    def load(self, path, actor):
        from openpyxl import load_workbook
        actor = self._actor(actor)
        if self.current_id and self.summary().status not in (Status.OK.value, Status.SKIPPED.value):
            raise ValueError("Phải hoàn tất code hiện tại trước khi nạp Excel khác")
        book = load_workbook(path, read_only=True, data_only=False)
        errors, values = [], {}
        try:
            rows = iter(book.active.iter_rows(values_only=True))
            headers = [str(v).strip() if v is not None else "" for v in next(rows, ())]
            if headers.count(self.code_column) != 1 or headers.count(self.qty_column) != 1:
                raise ValueError(f"Dòng 1: cần hai cột duy nhất '{self.code_column}', '{self.qty_column}'")
            ci, qi = headers.index(self.code_column), headers.index(self.qty_column)
            for line, row in enumerate(rows, 2):
                if all(v is None or v == "" for v in row):
                    continue
                code_value = row[ci] if ci < len(row) else None
                qty = row[qi] if qi < len(row) else None
                # A text code preserves leading zeroes; Excel formula cells are rejected.
                code = str(code_value).strip() if code_value is not None else ""
                if not code or code.startswith("="):
                    errors.append(f"Dòng {line}: code rỗng hoặc là công thức")
                    continue
                try:
                    number = Decimal(str(qty))
                    if isinstance(qty, bool) or not number.is_finite() or number <= 0 or number != number.to_integral_value() or number > 2147483647:
                        raise InvalidOperation
                    number = int(number)
                except (InvalidOperation, ValueError):
                    errors.append(f"Dòng {line} ({code}): qty phải là số nguyên dương")
                    continue
                if code in values and self.duplicates == "error":
                    errors.append(f"Dòng {line}: code trùng '{code}'")
                values[code] = values.get(code, 0) + number
        finally:
            book.close()
        if errors or not values:
            raise ValueError("\n".join(errors) if errors else "Excel không có code")
        with self.storage.transaction() as db:
            existing = self.storage.orders(self.work_date)
            if existing and {r["code"]: r["qty_expected"] for r in existing} != values:
                raise ValueError("Danh sách trong ngày đã tồn tại và khác file Excel; không được ghi đè lịch sử")
            if not existing:
                db.executemany("INSERT INTO orders(work_date,code,qty_expected,status) VALUES(?,?,?,?)",
                               [(self.work_date, c, q, Status.WAITING.value) for c, q in values.items()])
            self.storage.event("user", f"Nạp Excel: {path}; {len(values)} code", actor, db=db)
        self.actor = actor
        return self.snapshot()

    def select(self, code, actor):
        actor = self._actor(actor)
        if self.recount_id:
            raise ValueError("Đang chờ đếm lại tray")
        rows = self.storage.orders(self.work_date)
        target = next((r for r in rows if r["code"] == code), None)
        if target is None:
            raise ValueError("Code không có trong Excel của ngày")
        if self.current_id and target["id"] != self.current_id and self.summary().status not in (Status.OK.value, Status.SKIPPED.value):
            raise ValueError("Chỉ chuyển code khi code hiện tại OK hoặc được quản lý bỏ qua")
        # Restoring a session must not allow bypassing a persisted unfinished code.
        unfinished = [r for r in rows if r["status"] in (Status.COUNTING.value, Status.MISMATCH.value)]
        if any(r["id"] != target["id"] for r in unfinished):
            raise ValueError("Còn code đang đếm/lệch từ phiên trước; hãy xử lý code đó trước")
        if target["status"] in (Status.OK.value, Status.SKIPPED.value):
            raise ValueError("Code này đã hoàn tất")
        unavailable = self.registry.availability(code)
        if unavailable:
            raise ValueError(unavailable)
        with self.storage.transaction() as db:
            db.execute("UPDATE orders SET status=?,finished_at=NULL WHERE id=?", (Status.COUNTING.value, target["id"]))
            self.storage.event("user", f"Chọn code {code}", actor, db=db)
        self.current_id, self.actor = target["id"], actor
        if target["pending_cycle"]:
            # A crash/disk error after accepting a trigger must remain visible.
            self.storage.save_tray(target["id"], Result(0), None, actor,
                                   target["pending_replace_id"], ErrorCode.TIMEOUT)
            self.storage.event("worker", f"Khôi phục tray chưa hoàn tất của {code}: cần đếm lại", actor, "WARNING")
        return self.registry.get(code)

    def summary(self):
        if self.current_id is None:
            raise ValueError("Chưa chọn code")
        return self.storage.summary(self.current_id)

    def add_tray(self, result, image=None, error=0):
        if not self.current_id:
            raise ValueError("Chưa chọn code")
        tray_id = self.storage.save_tray(self.current_id, result, image, self.actor, self.recount_id, error)
        self.recount_id = None
        return tray_id

    def begin_tray(self):
        if self.summary().status != Status.COUNTING.value:
            raise ValueError("Code không ở trạng thái Đang đếm")
        with self.storage.transaction() as db:
            row = db.execute("SELECT pending_cycle FROM orders WHERE id=?", (self.current_id,)).fetchone()
            if row[0]:
                raise ValueError("Có tray chưa ghi xong; nạp lại code và đếm lại tray")
            db.execute("UPDATE orders SET pending_cycle=1,pending_replace_id=? WHERE id=?", (self.recount_id, self.current_id))

    def recount_last(self, actor):
        actor = self._actor(actor)
        summary = self.summary()
        if summary.status not in (Status.COUNTING.value, Status.MISMATCH.value):
            raise ValueError("Code đã hoàn tất")
        last = self.storage.last_tray(self.current_id)
        if not last:
            raise ValueError("Chưa có tray để đếm lại")
        with self.storage.transaction() as db:
            db.execute("UPDATE orders SET status=?,finished_at=NULL WHERE id=?", (Status.COUNTING.value, self.current_id))
            self.storage.event("user", f"Yêu cầu đếm lại tray {last['id']} của {summary.code}", actor, db=db)
        self.recount_id, self.actor = last["id"], actor

    def finish_code(self, actor, confirm_mismatch=False):
        actor = self._actor(actor)
        s = self.summary()
        if self.recount_id:
            raise ValueError("Cần đếm lại tray trước khi Xong code")
        if s.status != Status.COUNTING.value:
            raise ValueError("Code không ở trạng thái Đang đếm")
        with self.storage.lock:
            pending = self.storage.db.execute("SELECT pending_cycle FROM orders WHERE id=?", (self.current_id,)).fetchone()[0]
        if pending:
            raise ValueError("Còn tray chưa ghi xong; nạp lại code và đếm lại tray")
        if s.difference and not confirm_mismatch:
            raise ConfirmationRequired(f"{s.code}: {'thừa' if s.difference > 0 else 'thiếu'} {abs(s.difference)} sản phẩm. Xác nhận kết thúc ở trạng thái Lệch?")
        status = Status.OK if s.difference == 0 and s.n_foreign == s.n_lowconf == s.n_errors == 0 and s.n_trays > 0 else Status.MISMATCH
        with self.storage.transaction() as db:
            db.execute("UPDATE orders SET status=?,finished_at=? WHERE id=?", (status.value, datetime.now().isoformat(), self.current_id))
            self.storage.event("user", f"Xong {s.code}: {status.value}; đếm {s.qty_counted}/{s.qty_expected}; chênh {s.difference:+d}", actor, db=db)
        return self.summary()

    def skip_mismatch(self, actor, password, reason):
        actor = self._actor(actor)
        configured = os.environ.get("TRAY_MANAGER_PASSWORD", "")
        if not configured or not hmac.compare_digest(str(password), configured):
            raise ValueError("Sai mật khẩu quản lý hoặc chưa cấu hình TRAY_MANAGER_PASSWORD")
        if not str(reason).strip():
            raise ValueError("Cần ghi lý do bỏ qua")
        if self.summary().status != Status.MISMATCH.value:
            raise ValueError("Chỉ được bỏ qua code ở trạng thái Lệch")
        with self.storage.transaction() as db:
            db.execute("UPDATE orders SET status=?,finished_at=? WHERE id=?", (Status.SKIPPED.value, datetime.now().isoformat(), self.current_id))
            self.storage.event("user", f"Quản lý bỏ qua {self.summary().code}: {reason}", actor, level="WARNING", db=db)

    def next_code(self):
        rows = self.storage.orders(self.work_date)
        position = next((i for i, r in enumerate(rows) if r["id"] == self.current_id), -1)
        return next((r["code"] for r in rows[position + 1:] if r["status"] == Status.WAITING.value and not self.registry.availability(r["code"])), None)

    def snapshot(self):
        rows = self.storage.orders(self.work_date)
        for row in rows:
            row["model_error"] = self.registry.availability(row["code"])
        return {"orders": rows, "current": self.summary().__dict__ if self.current_id else None,
                "trays": self.storage.trays(self.current_id) if self.current_id else [],
                "recount_id": self.recount_id, "work_date": self.work_date}
