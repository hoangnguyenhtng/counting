"""Batch image cropping helpers used by the Teaching screen."""
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Sequence

import cv2
import numpy as np


IMAGE_EXTENSIONS = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff"})


@dataclass(frozen=True)
class CropRegion:
    name: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FolderCropResult:
    source_images: int
    saved_images: int
    failed_images: int
    output_folder: Path


def list_images(folder: Path | str, recursive: bool = False) -> list[Path]:
    """Return supported images from *folder* in a stable order.

    Teaching keeps the non-recursive default. Developer mode opts into
    recursive discovery so test datasets may be organized in subfolders.
    """
    folder = Path(folder)
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (
            path for path in candidates
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: str(path.relative_to(folder)).lower(),
    )


def read_image(path: Path | str):
    """Read an image while supporting Unicode paths on Windows."""
    try:
        encoded = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR) if encoded.size else None


def _safe_name(value: str, fallback: str) -> str:
    value = re.sub(r"[^\w.-]+", "_", (value or "").strip(), flags=re.UNICODE)
    return value.strip("._") or fallback


def _save_png(path: Path, image: np.ndarray) -> bool:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return False
    try:
        encoded.tofile(str(path))
        return True
    except OSError:
        return False


def crop_folder(
    source_folder: Path | str,
    regions: Sequence[CropRegion],
    reference_size: tuple[int, int],
    progress: Callable[[int, int, Path], None] | None = None,
) -> FolderCropResult:

    source_folder = Path(source_folder)
    images = list_images(source_folder)
    output_folder = source_folder / "CropImage"
    output_folder.mkdir(parents=True, exist_ok=True)

    reference_width, reference_height = reference_size
    if reference_width <= 0 or reference_height <= 0:
        raise ValueError("Reference image size must be positive.")
    if not regions:
        raise ValueError("At least one ROI is required.")

    saved = 0
    failed = 0
    total = len(images)
    for image_index, image_path in enumerate(images, start=1):
        image = read_image(image_path)
        if image is None:
            failed += len(regions)
        else:
            image_height, image_width = image.shape[:2]
            scale_x = image_width / reference_width
            scale_y = image_height / reference_height
            source_stem = _safe_name(image_path.stem, f"image_{image_index:04d}")

            for region_index, region in enumerate(regions, start=1):
                left = max(0, int(round(region.x * scale_x)))
                top = max(0, int(round(region.y * scale_y)))
                right = min(image_width, int(round((region.x + region.width) * scale_x)))
                bottom = min(image_height, int(round((region.y + region.height) * scale_y)))
                if right <= left or bottom <= top:
                    failed += 1
                    continue

                roi_name = _safe_name(region.name, f"ROI_{region_index}")
                output_path = output_folder / f"{source_stem}__{roi_name}_{region_index:02d}.png"
                if _save_png(output_path, image[top:bottom, left:right]):
                    saved += 1
                else:
                    failed += 1

        if progress is not None:
            progress(image_index, total, image_path)

    return FolderCropResult(total, saved, failed, output_folder)
