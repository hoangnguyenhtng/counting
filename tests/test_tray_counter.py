"""Production invariants exercised without a physical camera or PLC."""
import json
import time
from dataclasses import replace
from pathlib import Path
from threading import Event

import cv2
import numpy as np
import pytest
from openpyxl import Workbook, load_workbook

from tray_counter.core.camera import SimCamera
from tray_counter.core.models import CodeConfig, ConfirmationRequired, ErrorCode, Result, Status
from tray_counter.core.order_manager import OrderManager
from tray_counter.core.plc import SimPLC
from tray_counter.core.recorder import Recorder
from tray_counter.core.registry import Registry
from tray_counter.core.storage import Storage
from tray_counter.core.verifier import PerCodeVerifier, RegistryVerifier, SharedVerifier
from tray_counter.core.worker import Worker
from tray_counter.tools.evaluate import evaluate


def excel(path, rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    return path


def image(path, frame=None):
    frame = np.zeros((64, 64, 3), np.uint8) if frame is None else frame
    cv2.imencode(".png", frame)[1].tofile(str(path))
    return frame


@pytest.fixture
def system(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "A.onnx").touch()
    (tmp_path / "models" / "B.onnx").touch()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({c: {"model": f"models/{c}.onnx"} for c in ("A", "B")}), encoding="utf-8")
    registry = Registry(registry_path, tmp_path)
    storage = Storage(tmp_path / "data")
    orders = OrderManager(registry, storage, work_date="2026-11-02")
    yield orders, storage, tmp_path
    storage.close()


def prepare(system, qty=5):
    orders, _, root = system
    orders.load(excel(root / "orders.xlsx", [("code", "qty"), ("A", qty), ("B", 2)]), "operator")
    orders.select("A", "operator")
    return orders


def test_excel_validation_is_atomic_and_identifies_rows(system):
    orders, storage, root = system
    path = excel(root / "bad.xlsx", [("code", "qty"), ("A", 3), ("A", 1), ("B", 0), ("C", 1.5)])
    with pytest.raises(ValueError) as caught:
        orders.load(path, "operator")
    assert "Dòng 3" in str(caught.value) and "Dòng 4" in str(caught.value) and "Dòng 5" in str(caught.value)
    assert storage.orders(orders.work_date) == []
    orders.load(excel(root / "missing.xlsx", [("code", "qty"), ("NO_MODEL", 2)]), "operator")
    assert orders.snapshot()["orders"][0]["model_error"]
    with pytest.raises(ValueError, match="Chưa có model"):
        orders.select("NO_MODEL", "operator")


def test_excel_merge_and_text_code_preservation(system):
    orders, _, root = system
    orders.duplicates = "merge"
    orders.load(excel(root / "merge.xlsx", [("code", "qty"), ("001", 2), ("001", "3")]), "operator")
    row = orders.snapshot()["orders"][0]
    assert row["code"] == "001" and row["qty_expected"] == 5


def test_registry_duplicate_codes_are_not_silently_overwritten(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text('{"A":{"model":"a.onnx"},"A":{"model":"b.onnx"}}')
    with pytest.raises(ValueError, match="khóa trùng"):
        Registry(path)


@pytest.mark.parametrize("qty", [True, "NaN", "Infinity", "=2+3", -1, 0, 2.1, None])
def test_invalid_excel_quantities_are_rejected(system, qty):
    orders, _, root = system
    with pytest.raises(ValueError, match="Dòng 2"):
        orders.load(excel(root / "invalid.xlsx", [("code", "qty"), ("A", qty)]), "operator")


def test_counts_multiple_trays_and_only_compares_excel_on_finish(system):
    orders = prepare(system, 5)
    orders.add_tray(Result(2))
    assert orders.summary().qty_counted == 2
    with pytest.raises(ValueError, match="Chỉ chuyển"):
        orders.select("B", "operator")
    orders.add_tray(Result(3))
    assert orders.finish_code("operator").status == Status.OK.value
    orders.select("B", "operator")
    assert orders.summary().code == "B"


def test_recount_replaces_latest_and_audits_identity(system):
    orders = prepare(system, 5)
    first = orders.add_tray(Result(2))
    old = orders.add_tray(Result(4, n_foreign=1))
    orders.recount_last("rework-user")
    new = orders.add_tray(Result(3))
    assert orders.summary().qty_counted == 5
    assert orders.summary().n_foreign == 0
    rows = orders.storage.trays(orders.current_id)
    assert next(r for r in rows if r["id"] == old)["replaced_by"] == new
    assert next(r for r in rows if r["id"] == first)["replaced_by"] is None
    assert rows[0]["seq"] == 2 and rows[1]["seq"] == 2
    assert orders.storage.db.execute("SELECT COUNT(*) FROM events WHERE actor='rework-user'").fetchone()[0] == 2
    assert orders.finish_code("operator").status == Status.OK.value


@pytest.mark.parametrize("result", [Result(5, n_foreign=1), Result(5, n_lowconf=1)])
def test_exact_total_never_overrides_foreign_or_lowconf(system, result):
    orders = prepare(system)
    orders.add_tray(result)
    assert orders.finish_code("operator").status == Status.MISMATCH.value
    with pytest.raises(ValueError):
        orders.select("B", "operator")


def test_finish_mismatch_confirmation_and_manager_skip(system, monkeypatch):
    orders = prepare(system)
    orders.add_tray(Result(4))
    with pytest.raises(ConfirmationRequired, match="thiếu 1"):
        orders.finish_code("operator")
    assert orders.summary().status == Status.COUNTING.value
    assert orders.finish_code("operator", True).status == Status.MISMATCH.value
    with pytest.raises(ValueError, match="mật khẩu"):
        orders.skip_mismatch("operator", "1", "test")
    monkeypatch.setenv("TRAY_MANAGER_PASSWORD", "secret")
    orders.skip_mismatch("manager", "secret", "sản xuất cho phép bỏ qua")
    assert orders.summary().status == Status.SKIPPED.value
    orders.select("B", "operator")


def test_restore_session_cannot_bypass_unfinished_or_interrupted_tray(system):
    orders = prepare(system)
    orders.begin_tray()
    restored = OrderManager(orders.registry, orders.storage, work_date=orders.work_date)
    with pytest.raises(ValueError, match="phiên trước"):
        restored.select("B", "operator")
    restored.select("A", "operator")
    assert restored.summary().n_errors == 1
    restored.recount_last("operator")
    restored.add_tray(Result(5))
    assert restored.finish_code("operator").status == Status.OK.value


def test_original_png_ng_annotation_report_and_retention(system):
    orders = prepare(system)
    frame = np.random.default_rng(12).integers(0, 256, (48, 65, 3), dtype=np.uint8)
    orders.add_tray(Result(5, n_foreign=1, boxes=[(2, 2, 20, 20, "foreign", 0.9)]), frame)
    row = orders.storage.last_tray(orders.current_id)
    assert np.array_equal(cv2.imdecode(np.fromfile(row["image_path"], np.uint8), cv2.IMREAD_COLOR), frame)
    assert Path(row["annotated_path"]).is_file()
    report = orders.storage.export_report(orders.work_date)
    book = load_workbook(report, read_only=True)
    assert book.sheetnames == ["Orders", "Trays", "Events"]
    book.close()
    folder = orders.storage.root / "images" / "20000101"
    folder.mkdir(parents=True)
    (folder / "old.png").touch()
    orders.storage.cleanup_images(30)
    assert not folder.exists()


class FakeVerifier:
    def __init__(self, results):
        self.results, self.loaded, self.calls = iter(results), [], 0

    def load(self, code):
        self.loaded.append(code)

    def count_and_verify(self, frame, code):
        self.calls += 1
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return value


def spin(worker, predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        worker.step()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("Worker did not reach expected state")


def make_worker(system, results, **kwargs):
    orders = prepare(system)
    folder = system[2] / "images"
    folder.mkdir()
    for seq in range(6):
        image(folder / f"{seq}.png")
    camera, plc, verifier = SimCamera(folder), SimPLC(), FakeVerifier(results)
    worker = Worker(plc, camera, verifier, orders, **kwargs)
    worker.step()  # Observe TRIGGER low before arming.
    worker.submit("select", code="A", actor="operator")
    spin(worker, lambda: worker.ready)
    return worker


def cycle(worker):
    worker.plc.set_trigger(True)
    spin(worker, lambda: worker.plc.snapshot()["DONE"] == 1)


def reset(worker):
    worker.plc.set_trigger(False)
    worker.step()
    assert worker.plc.snapshot()["DONE"] == worker.plc.snapshot()["RESULT"] == 0


def test_full_trigger_sequence_holds_done_and_counts_once_per_edge(system):
    worker = make_worker(system, [Result(2), Result(3)])
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 1
    for _ in range(10):
        worker.step()
    assert worker.verifier.calls == 1 and worker.camera.index == 1
    assert worker.plc.snapshot()["DONE"] == 1
    history = list(worker.plc.history)
    done_index = max(i for i, item in enumerate(history) if item == ("DONE", 1))
    assert history[done_index - 3] == ("RESULT", 1)
    reset(worker)
    cycle(worker)
    assert worker.orders.summary().qty_counted == 5
    reset(worker)
    worker.submit("finish", actor="operator")
    worker.step()
    assert worker.orders.summary().status == Status.OK.value


def test_foreign_always_sends_plc_ng_even_with_exact_excel_total(system):
    worker = make_worker(system, [Result(5, n_foreign=1)])
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 2
    reset(worker)
    worker.submit("finish", actor="operator")
    worker.step()
    assert worker.orders.summary().status == Status.MISMATCH.value


def test_worker_recount_replaces_previous_capture_without_double_count(system):
    worker = make_worker(system, [Result(2), Result(4, n_foreign=1), Result(3)])
    cycle(worker)
    reset(worker)
    cycle(worker)
    reset(worker)
    worker.submit("recount", actor="rework")
    worker.step()
    assert worker.orders.recount_id
    cycle(worker)
    reset(worker)
    assert worker.orders.summary().qty_counted == 5
    assert worker.orders.summary().n_foreign == 0
    assert len(worker.orders.storage.trays(worker.orders.current_id)) == 3
    worker.submit("finish", actor="operator")
    worker.step()
    assert worker.orders.summary().status == Status.OK.value


def test_no_code_trigger_does_not_capture_and_returns_error(system):
    orders, _, root = system
    image(root / "x.png")
    camera = SimCamera(root)
    worker = Worker(SimPLC(), camera, FakeVerifier([]), orders)
    worker.step()
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 3
    assert worker.plc.snapshot()["ERR_CODE"] == ErrorCode.NOT_READY
    assert camera.index == 0


def test_model_loading_rejects_trigger_and_does_not_reprocess_held_high(system):
    worker = make_worker(system, [Result(5)])
    gate = Event()
    worker.verifier.load = lambda code: gate.wait(2)
    worker.submit("select", code="A", actor="operator")
    worker.step()
    assert worker.loading and not worker.ready
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 3 and worker.verifier.calls == 0
    gate.set()
    spin(worker, lambda: worker.ready)
    worker.step()
    assert worker.verifier.calls == 0
    reset(worker)
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 1


@pytest.mark.parametrize("failure", ["camera", "model", "storage"])
def test_operating_failures_never_send_ok(system, monkeypatch, failure):
    worker = make_worker(system, [RuntimeError("bad model") if failure == "model" else Result(5)])
    if failure == "camera":
        monkeypatch.setattr(worker.camera, "grab", lambda: (_ for _ in ()).throw(IOError("bad camera")))
    if failure == "storage":
        monkeypatch.setattr(worker.orders.storage, "_write_png", lambda *args: (_ for _ in ()).throw(IOError("disk full")))
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 3
    assert worker.orders.summary().qty_counted == 0
    assert not any(item == ("RESULT", 1) for item in worker.plc.history)


def test_timeout_discards_late_success_and_stops_heartbeat_until_return(system):
    worker = make_worker(system, [Result(5)], cycle_timeout_s=0.02, heartbeat_s=0.005)
    gate = Event()
    worker.verifier.count_and_verify = lambda *args: (gate.wait(2), Result(5))[1]
    cycle(worker)
    assert worker.plc.snapshot()["RESULT"] == 3
    assert worker.plc.snapshot()["ERR_CODE"] == ErrorCode.TIMEOUT
    heartbeat = worker.plc.snapshot()["HEARTBEAT"]
    time.sleep(0.01)
    worker.step()
    assert worker.plc.snapshot()["HEARTBEAT"] == heartbeat
    gate.set()
    spin(worker, lambda: worker._quarantined is None)
    assert worker.orders.summary().qty_counted == 0 and not worker.ready
    assert worker.plc.snapshot()["RESULT"] == 3


def test_early_trigger_drop_cannot_reuse_late_result(system):
    worker = make_worker(system, [Result(5)])
    gate = Event()
    worker.verifier.count_and_verify = lambda *args: (gate.wait(2), Result(5))[1]
    worker.plc.set_trigger(True)
    worker.step()
    assert worker.busy
    worker.plc.set_trigger(False)
    worker.step()
    gate.set()
    spin(worker, lambda: worker._quarantined is None)
    assert worker.orders.summary().qty_counted == 0 and worker.orders.summary().n_errors == 1
    assert not any(item == ("RESULT", 1) for item in worker.plc.history)


def onnx_model(path, rows, classes):
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper, numpy_helper
    output = np.asarray(rows, np.float32).T[None]
    graph = helper.make_graph([helper.make_node("Constant", [], ["detections"], value=numpy_helper.from_array(output))],
        "test-only-detector", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 64, 64])],
        [helper.make_tensor_value_info("detections", TensorProto.FLOAT, list(output.shape))])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    model.metadata_props.add(key="names", value=str(dict(enumerate(classes))))
    onnx.save(model, str(path))


def test_real_onnx_full_frame_nms_tray_filter_and_confidence(tmp_path):
    rows = [[16, 16, 10, 10, .95, .01, .01], [16, 16, 10, 10, .85, .01, .01],
            [40, 40, 10, 10, .01, .95, .01], [16, 40, 8, 8, .35, .01, .01],
            [1, 1, 2, 2, .99, .01, .01], [32, 32, 54, 54, .01, .01, .99]]
    path = tmp_path / "actual.onnx"
    onnx_model(path, rows, ("target", "other", "tray"))
    cfg = CodeConfig("A", model=str(path), classes=("target", "other", "tray"), tray_mode="class")
    result = PerCodeVerifier(cfg).count_and_verify(np.zeros((64, 64, 3), np.uint8))
    assert (result.count, result.n_foreign, result.n_lowconf) == (1, 1, 1)
    assert len(result.boxes) == 3 and not result.ok_tray
    with pytest.raises(ValueError, match="Classes"):
        PerCodeVerifier(replace(cfg, classes=("other", "target", "tray")))


def test_no_detected_tray_fails_closed(tmp_path):
    path = tmp_path / "empty.onnx"
    onnx_model(path, [[16, 16, 10, 10, .95, .01]], ("target", "other"))
    verifier = PerCodeVerifier(CodeConfig("A", model=str(path)))
    with pytest.raises(ValueError, match="vùng tray"):
        verifier.count_and_verify(np.zeros((64, 64, 3), np.uint8))


def test_prefetch_keeps_two_models_and_preserves_active_model(tmp_path):
    models = {}
    for code in ("A", "B", "C"):
        path = tmp_path / f"{code}.onnx"
        onnx_model(path, [[16, 16, 10, 10, .95, .01]], ("target", "other"))
        models[code] = {"model": str(path)}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(models))
    verifier = RegistryVerifier(Registry(path, tmp_path))
    verifier.load("A")
    verifier.prefetch("B")
    verifier.load("B")
    verifier.prefetch("C")
    assert set(verifier.cache) == {"B", "C"} and verifier.active == "B"


def test_blocked_video_encoder_does_not_block_counting(system, monkeypatch):
    worker = make_worker(system, [Result(5)])
    image_ready, unblock = Event(), Event()

    class SlowWriter:
        def isOpened(self):
            return True

        def write(self, frame):
            image_ready.set()
            unblock.wait(2)

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoWriter", lambda *args: SlowWriter())
    worker.camera.grab()  # Seed the cache; recorder never advances replay.
    recorder = Recorder(worker.camera, system[2] / "videos", fps=100)
    recorder.start()
    try:
        assert image_ready.wait(1)
        cycle(worker)
        assert worker.plc.snapshot()["RESULT"] == 1
        assert worker.camera.index == 2
    finally:
        unblock.set()
        recorder.stop()


def test_real_shared_embedding_recognizes_target_and_foreign(tmp_path):
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper
    detector = tmp_path / "product.onnx"
    onnx_model(detector, [[16, 16, 10, 10, .95, .01], [40, 40, 10, 10, .95, .01],
                          [32, 32, 58, 58, .01, .99]], ("product", "tray"))
    graph = helper.make_graph([helper.make_node("ReduceMean", ["images"], ["vector"], axes=[2, 3], keepdims=0)],
        "test-only-embedding", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 8, 8])],
        [helper.make_tensor_value_info("vector", TensorProto.FLOAT, [1, 3])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    embedding = tmp_path / "embedding.onnx"
    onnx.save(model, str(embedding))
    refs = tmp_path / "reference"
    refs.mkdir()
    red = np.full((8, 8, 3), (0, 0, 255), np.uint8)
    image(refs / "red.png", red)
    cfg = CodeConfig("A", mode="shared", model=str(detector), embedding_model=str(embedding), ref_dir=str(refs),
                     classes=("product", "tray"), tray_mode="class", embedding_size=8,
                     embedding_mean=(.1, .1, .1), embedding_std=(1, 1, 1))
    frame = np.zeros((64, 64, 3), np.uint8)
    frame[11:21, 11:21] = (0, 0, 255)
    frame[35:45, 35:45] = (0, 255, 0)
    result = SharedVerifier(cfg).count_and_verify(frame)
    assert result.count == 1 and result.n_foreign == 1 and not result.ok_tray


def test_evaluator_reports_missed_foreign_and_errors(system):
    orders, _, root = system
    image(root / "a.png")
    fake = FakeVerifier([Result(5), RuntimeError("bad model")])
    report = evaluate(orders.registry,
        [{"code": "A", "image": "a.png", "count": 5, "n_foreign": 1},
         {"code": "A", "image": "a.png", "count": 5}], root, fake)
    metrics = report["metrics"]["per_code"]
    assert metrics["missed_fault_rate"] == 1 and metrics["errors"] == 1 and metrics["false_ng_rate"] == 1
