from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
import numpy as np

@dataclass
class DetectionResult:
    model_name: str = ""
    src: Optional[np.ndarray] = None
    dst: Optional[np.ndarray] = None
    ret: str = ""                    # "PASS", "FAIL"
    label_counts: str = ""           # Status description
    timecheck: float = 0.0           # execution time in milliseconds
    error: str = ""

