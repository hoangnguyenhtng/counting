"""Evaluate a labeled image manifest with the exact production verifier."""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from ..core.registry import Registry
from ..core.storage import read_image
from ..core.verifier import RegistryVerifier


def evaluate(registry, manifest, base_dir, verifier=None):
    verifier = verifier or RegistryVerifier(registry)
    results, groups = [], {}
    for item in manifest:
        code = item["code"]
        cfg = registry.get(code)
        expected_count, expected_foreign = item["count"], item.get("n_foreign", 0)
        fault = bool(item.get("fault", False) or expected_foreign or item.get("n_lowconf", 0) or expected_count == 0)
        group = groups.setdefault(cfg.mode, dict(n=0, count_correct=0, foreign_cases=0, foreign_detected=0,
                                                good_cases=0, false_ng=0, fault_cases=0, missed_fault=0, errors=0,
                                                infer_ms=[], load_ms=[]))
        group["n"] += 1
        group["fault_cases" if fault else "good_cases"] += 1
        group["foreign_cases"] += int(expected_foreign > 0)
        row = {"code": code, "image": item["image"], "mode": cfg.mode}
        try:
            started = perf_counter()
            verifier.load(code)
            group["load_ms"].append((perf_counter() - started) * 1000)
            result = verifier.count_and_verify(read_image(Path(base_dir) / item["image"]), code)
            group["count_correct"] += int(result.count == expected_count)
            group["foreign_detected"] += int(expected_foreign > 0 and result.n_foreign > 0)
            group["false_ng"] += int(not fault and not result.ok_tray)
            group["missed_fault"] += int(fault and result.ok_tray)
            group["infer_ms"].append(result.infer_ms)
            row.update(count=result.count, n_foreign=result.n_foreign, n_lowconf=result.n_lowconf,
                       result=1 if result.ok_tray else 2, infer_ms=result.infer_ms)
        except Exception as exc:
            group["errors"] += 1
            group["false_ng"] += int(not fault)
            row.update(result=3, error=str(exc))
        results.append(row)
    metrics = {}
    for mode, group in groups.items():
        metrics[mode] = {k: v for k, v in group.items() if not isinstance(v, list)}
        for name, numerator, denominator in (("count_accuracy", "count_correct", "n"),
                ("foreign_recall", "foreign_detected", "foreign_cases"),
                ("false_ng_rate", "false_ng", "good_cases"), ("missed_fault_rate", "missed_fault", "fault_cases")):
            metrics[mode][name] = group[numerator] / group[denominator] if group[denominator] else None
        for name in ("infer_ms", "load_ms"):
            metrics[mode][name + "_p50"] = float(np.percentile(group[name], 50)) if group[name] else None
            metrics[mode][name + "_p95"] = float(np.percentile(group[name], 95)) if group[name] else None
    return {"metrics": metrics, "images": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON list: image, code, count, n_foreign, n_lowconf, fault")
    parser.add_argument("--registry", type=Path, default=Path("tray_counter/config/registry.json"))
    parser.add_argument("--output", type=Path, default=Path("evaluation.json"))
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, list) or not manifest:
        parser.error("Manifest phải là danh sách ảnh có nhãn, không rỗng")
    report = evaluate(Registry(args.registry), manifest, args.manifest.parent)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2, ensure_ascii=False))
    # A run consisting of system errors must never pass a zero-miss check.
    return int(any(m["missed_fault"] or m["errors"] for m in report["metrics"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
