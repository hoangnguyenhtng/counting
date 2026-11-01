"""Register validated two-class ONNX detectors; preserve existing entries."""
import argparse
import json
from pathlib import Path

from ..core.registry import Registry
from ..core.verifier import Detector


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", type=Path)
    parser.add_argument("--output", type=Path, default=Path("tray_counter/config/registry.json"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--tray-class", action="store_true")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    root = output.parent.parent
    entries = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else {}
    classes = ["target", "other", "tray"] if args.tray_class else ["target", "other"]
    models = sorted(args.models.glob("*.onnx"))
    if not models:
        parser.error("Không tìm thấy file ONNX")
    for model in models:
        if model.stem not in entries:
            import os
            entries[model.stem] = {"mode": "per_code", "model": os.path.relpath(model.resolve(), root).replace("\\", "/"),
                                   "version": args.version, "conf": 0.5, "conf_low": 0.25,
                                   "classes": classes, "tray_mode": "class" if args.tray_class else "contour"}
    temporary = output.with_suffix(".tmp.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        registry = Registry(temporary, root)
        for model in models:
            Detector(registry.get(model.stem))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Đã kiểm tra model và cập nhật {output}")


if __name__ == "__main__":
    main()
