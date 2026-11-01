"""PLC handshake loop, independent of Qt and serialized production mutations."""
from concurrent.futures import Future
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic

from .models import ConfirmationRequired, ErrorCode, Result, Status


def background(function):
    # A timed-out native SDK/ONNX call cannot be killed safely. Daemon tasks
    # cannot keep the process alive; their results are discarded after timeout.
    future = Future()

    def run():
        try:
            future.set_result(function())
        except BaseException as exc:
            future.set_exception(exc)

    Thread(target=run, daemon=True, name="tray-task").start()
    return future


class CycleFailure(Exception):
    def __init__(self, error, message, image=None):
        super().__init__(message)
        self.error, self.image = error, image


class Worker:
    def __init__(self, plc, camera, verifier, orders, on_event=None, recorder=None,
                 heartbeat_s=0.5, cycle_timeout_s=10, model_timeout_s=60, poll_ms=10):
        if heartbeat_s <= 0 or cycle_timeout_s <= 0 or model_timeout_s <= 0 or poll_ms <= 0:
            raise ValueError("Timeout và heartbeat phải > 0")
        self.plc, self.camera, self.verifier, self.orders = plc, camera, verifier, orders
        self.storage, self.recorder = orders.storage, recorder
        self.on_event = on_event or (lambda kind, value: None)
        self.heartbeat_s, self.cycle_timeout_s, self.model_timeout_s = heartbeat_s, cycle_timeout_s, model_timeout_s
        self.poll_s = poll_ms / 1000
        self.commands, self._stop, self._thread = Queue(), Event(), None
        self.ready, self.loading, self.busy = False, False, False
        self._previous, self._armed, self._done = False, False, False
        self._job, self._prefetch, self._heartbeat_at = None, None, 0
        self._quarantined = None
        self._quarantine_kind = None

    def emit(self, kind, value):
        self.on_event(kind, value)

    def submit(self, command, **arguments):
        self.commands.put((command, arguments))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.plc.write_result(0, False, False)
        if self.recorder:
            self.recorder.start()
        self._thread = Thread(target=self.run, daemon=True, name="tray-worker")
        self._thread.start()

    def state(self):
        state = self.orders.snapshot()
        state.update(ready=self.ready, loading=self.loading, busy=self.busy, working=self._job is not None,
                     stalled=self._quarantined is not None)
        self.emit("state", state)

    def log(self, source, message, level="ERROR"):
        self.storage.event(source, message, level=level)
        self.emit("log", message)

    def _start_job(self, kind, function, timeout):
        self._job = (kind, background(function), monotonic(), timeout)

    def _command(self, name, args):
        if name == "export":
            if self._job:
                raise ValueError("Đang xử lý; hãy xuất báo cáo sau")
            self._start_job("export", lambda: self.storage.export_report(self.orders.work_date, args.get("path")), 60)
            self.state()
            return
        if self._job or self._quarantined or self.plc.read_trigger() or self._done:
            raise ValueError("Chờ xử lý xong và hạ TRIGGER trước khi thao tác")
        if name == "load":
            self.orders.load(args["path"], args["actor"])
        elif name == "select":
            self.orders.select(args["code"], args["actor"])
            self.ready, self.loading = False, True
            self._start_job("model", lambda: self.verifier.load(args["code"]), self.model_timeout_s)
        elif name == "finish":
            if not self.ready:
                raise ValueError("Cần nạp model và xử lý lỗi trước khi Xong code")
            self.orders.finish_code(args["actor"], args.get("confirm_mismatch", False))
            self.ready = False
        elif name == "recount":
            self.orders.recount_last(args["actor"])
            # The same warmed model is still selected; a model fault requires reload.
            if not self.ready:
                self.loading = True
                self._start_job("model", lambda: self.verifier.load(self.orders.summary().code), self.model_timeout_s)
        elif name == "skip":
            self.orders.skip_mismatch(args["actor"], args["password"], args["reason"])
            self.ready = False
        elif name == "camera":
            factory = args["factory"]
            old_camera, recorder = self.camera, self.recorder

            def change_camera():
                if recorder:
                    recorder.stop()
                old_camera.close()
                return factory()

            self._start_job("camera", change_camera, 15)
        else:
            raise ValueError(f"Lệnh không hỗ trợ: {name}")
        self.state()

    def _publish(self, result, error=ErrorCode.NONE):
        self.plc.write_result(result, True, False, error)
        self._done, self.busy = True, False
        self.emit("plc", {"RESULT": result, "ERR_CODE": int(error), "DONE": 1, "BUSY": 0})

    def _capture(self, code):
        try:
            image = self.camera.grab()
            if image is None or not image.size:
                raise IOError("Camera trả ảnh rỗng")
        except Exception as exc:
            raise CycleFailure(ErrorCode.CAMERA, str(exc)) from exc
        try:
            result = self.verifier.count_and_verify(image, code)
            if not isinstance(result, Result):
                raise ValueError("Verifier không trả về Result")
            return image, result
        except Exception as exc:
            raise CycleFailure(ErrorCode.MODEL, str(exc), image) from exc

    def _cycle_error(self, error, message, image=None):
        if error in (ErrorCode.MODEL, ErrorCode.STORAGE, ErrorCode.TIMEOUT):
            self.ready = False
        try:
            self.log("camera" if error == ErrorCode.CAMERA else "model" if error == ErrorCode.MODEL else "worker", message)
            if self.orders.current_id and self.orders.summary().status == Status.COUNTING.value:
                self.orders.add_tray(Result(0), image, error)
        except Exception as exc:
            error, self.ready = ErrorCode.STORAGE, False
            self.emit("log", f"Lỗi ghi dữ liệu: {exc}")
        self._publish(3, error)
        self.state()

    def _poll_job(self):
        if not self._job:
            return
        kind, future, started, timeout = self._job
        if not future.done():
            if monotonic() - started <= timeout:
                return
            self._job, self.loading = None, False
            # Do not permit new work while an uninterruptible SDK/model call is running.
            self._quarantined = future
            self._quarantine_kind = kind
            if kind == "cycle":
                self._cycle_error(ErrorCode.TIMEOUT, f"Quá thời gian xử lý tray ({timeout:g}s)")
            else:
                self.ready = False
                self.log("model", f"Quá thời gian {kind} ({timeout:g}s); cần kiểm tra thiết bị/model")
                self.state()
            return
        self._job = None
        try:
            value = future.result()
            if kind == "model":
                self.loading, self.ready = False, True
                self.log("model", f"Model {self.orders.summary().code} đã nạp và warm-up ({(monotonic() - started) * 1000:.0f} ms)", "INFO")
                next_code = self.orders.next_code()
                if next_code and not self._prefetch:
                    prefetch = getattr(self.verifier, "prefetch", None)
                    if prefetch:
                        self._prefetch = (next_code, background(lambda: prefetch(next_code)), monotonic())
            elif kind == "cycle":
                image, result = value
                try:
                    self.orders.add_tray(result, image)
                except Exception as exc:
                    self._cycle_error(ErrorCode.STORAGE, str(exc), image)
                    return
                self._publish(1 if result.ok_tray else 2)
                self.emit("log", f"{self.orders.summary().code}: hoàn tất tray trong {(monotonic() - started) * 1000:.0f} ms")
                self.emit("tray", {"image": image, "result": result})
                if result.n_foreign or result.n_lowconf:
                    self.emit("log", "Có sản phẩm lạ/không chắc chắn: kiểm tra code đang chọn và đếm lại tray")
            elif kind == "camera":
                self.camera = value
                if self.recorder:
                    self.recorder.camera = value
                    self.recorder.start()
                self.log("camera", "Đã đổi nguồn ảnh", "INFO")
            elif kind == "export":
                self.emit("report", value)
        except CycleFailure as exc:
            self._cycle_error(exc.error, str(exc), exc.image)
        except Exception as exc:
            self.loading, self.ready = False, False
            if kind == "cycle":
                self._cycle_error(ErrorCode.MODEL, str(exc))
            else:
                self.log("model" if kind == "model" else "worker", str(exc))
                self.emit("error", str(exc))
        self.state()

    def step(self):
        self._poll_job()
        if self._quarantined and self._quarantined.done():
            # Drop every late result. Reloading the code is required to resume.
            if self._quarantine_kind == "camera":
                try:
                    self._quarantined.result().close()
                except Exception:
                    pass
            self._quarantined = None
            self._quarantine_kind = None
            self.state()
        if self._prefetch and self._prefetch[1].done():
            code, future, _ = self._prefetch
            self._prefetch = None
            try:
                future.result()
            except Exception as exc:
                self.log("model", f"Nạp trước {code} thất bại: {exc}", "WARNING")
        for _ in range(10):
            try:
                command, args = self.commands.get_nowait()
            except Empty:
                break
            try:
                self._command(command, args)
            except ConfirmationRequired as exc:
                self.emit("confirmation", {"message": str(exc), "actor": args["actor"]})
            except Exception as exc:
                self.storage.event("user", f"Từ chối thao tác {command}: {exc}", args.get("actor", "system"), "WARNING")
                self.emit("error", str(exc))
        trigger = self.plc.read_trigger()
        if self.busy and not trigger and self._job and self._job[0] == "cycle":
            # The PLC must keep TRIGGER high until DONE. Discard a late result
            # rather than attributing it to a subsequent rising edge.
            self._quarantined, self._quarantine_kind = self._job[1], "cycle"
            self._job = None
            self.ready = False
            self._cycle_error(ErrorCode.PLC, "PLC hạ TRIGGER trước DONE; kết quả đang xử lý đã bị hủy")
        if not trigger:
            self._armed = True
            if self._done:
                self.plc.write_result(0, False, False)
                self._done = False
                self.emit("plc", {"RESULT": 0, "DONE": 0, "BUSY": 0, "ERR_CODE": 0})
        if trigger and not self._previous:
            if not self._armed or not self.ready or self._job or self._quarantined or not self.orders.current_id or self.orders.summary().status != Status.COUNTING.value:
                self.log("plc", "TRIGGER khi chưa sẵn sàng; không chụp", "WARNING")
                self._publish(3, ErrorCode.NOT_READY)
            else:
                try:
                    self.orders.begin_tray()
                except Exception as exc:
                    self._cycle_error(ErrorCode.STORAGE, str(exc))
                    self._previous = trigger
                    return
                self.plc.write_result(0, False, True)
                self.busy = True
                code = self.orders.summary().code
                self._start_job("cycle", lambda: self._capture(code), self.cycle_timeout_s)
                self.state()
        self._previous = trigger
        now = monotonic()
        # An SDK/model that exceeded its deadline must not appear healthy to the PLC.
        if not self._quarantined and now - self._heartbeat_at >= self.heartbeat_s:
            self.plc.heartbeat()
            self._heartbeat_at = now

    def run(self):
        self.state()
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as exc:
                self.ready = False
                self.emit("error", f"Worker/PLC: {exc}")
                try:
                    self.log("plc", str(exc))
                    self._publish(3, ErrorCode.PLC)
                except Exception:
                    pass
                # A transport failure terminates the loop, stopping HEARTBEAT.
                self._stop.set()
            self._stop.wait(self.poll_s)

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                raise TimeoutError("Worker chưa dừng; không được đóng dữ liệu/camera")
        if self.recorder:
            self.recorder.stop()
        if self._job or self._quarantined:
            # Capture tasks never write to storage; stopping the loop discards them.
            self.ready = False
        if self._job and self._job[0] == "export":
            self._job[1].result(timeout=timeout)
        for job in (self._job, (self._quarantine_kind, self._quarantined) if self._quarantined else None):
            if job and job[0] == "camera":
                def close_late_camera(future):
                    try:
                        future.result().close()
                    except Exception:
                        pass
                job[1].add_done_callback(close_late_camera)
        try:
            if self.plc.read_trigger():
                self.plc.write_result(3, True, False, ErrorCode.NOT_READY)
            else:
                self.plc.write_result(0, False, False)
        finally:
            self.plc.close()
            self.camera.close()
