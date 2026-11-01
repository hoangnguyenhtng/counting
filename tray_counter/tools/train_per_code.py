"""Train/export local YOLOv8/YOLO11 two-class datasets; no implicit weight download."""
import argparse
import json
import shutil
from pathlib import Path

import yaml


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help='JSON mapping code -> local dataset.yaml')
    parser.add_argument("--weights", type=Path, required=True, help="Local YOLOv8/YOLO11 .pt weights")
    parser.add_argument("--output", type=Path, default=Path("tray_counter/models"))
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    if not args.weights.is_file():
        parser.error("Cần weights đã có trên máy; script không tải model")
    import os
    os.environ["YOLO_AUTOINSTALL"] = "false"
    from ultralytics import YOLO
    from ..core.models import CodeConfig
    from ..core.verifier import Detector
    datasets = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    prepared = []
    for code, dataset in datasets.items():
        if not code or Path(code).name != code or any(c in code for c in '<>:"/\\|?*'):
            raise ValueError(f"Tên code không hợp lệ cho tên model: {code}")
        data = (args.manifest.parent / dataset).resolve()
        config = yaml.safe_load(data.read_text(encoding="utf-8-sig"))
        names = config["names"]
        names = [names[k] for k in sorted(names, key=int)] if isinstance(names, dict) else names
        if names not in (["target", "other"], ["target", "other", "tray"]):
            raise ValueError(f"{code}: dataset cần target, other và tùy chọn tray, đúng thứ tự")
        destination = args.output / f"{code}.onnx"
        if destination.exists():
            raise FileExistsError(f"Model đã tồn tại; chọn thư mục version mới: {destination}")
        prepared.append((code, data, names, destination))
    args.output.mkdir(parents=True, exist_ok=True)
    for code, data, names, destination in prepared:
        model = YOLO(str(args.weights.resolve()))
        model.train(data=str(data), epochs=args.epochs, imgsz=args.imgsz, device=args.device,
                    project=str(args.output / "training"), name=code, exist_ok=False)
        best = YOLO(str(model.trainer.best))
        exported = Path(best.export(format="onnx", imgsz=args.imgsz, batch=1, dynamic=False, half=False,
                                    nms=False, simplify=False, device=args.device))
        cfg = CodeConfig(code, model=str(exported), classes=tuple(names), input_size=args.imgsz,
                         tray_mode="class" if "tray" in names else "contour")
        Detector(cfg)  # Reject incompatible exports before publishing the model.
        shutil.copy2(exported, destination)
        print(f"{code}: {destination}")


if __name__ == "__main__":
    main()
