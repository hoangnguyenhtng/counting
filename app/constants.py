from enum import Enum

class Step(Enum):
    READ_TRIGGER = "STEP_READ_TRIGGER"
    ON_LIGHTING = "STEP_ON_LIGHTING"
    CAPTURE = "STEP_CAPTURE"
    OFF_LIGHTING = "STEP_OFF_LIGHTING"
    DETECT = "STEP_DETECT"
    OUTPUT = "STEP_OUTPUT"
    WRITE_LOG = "STEP_WRITE_LOG"
    RELEASE = "STEP_RELEASE"
    ERROR = "STEP_ERROR"

class StepResult(Enum):
    WAIT = "WAIT"
    PASS_ = "PASS"
    FAIL = "FAIL"

class CameraType(Enum):
    HIK = "HIK"
    SODA = "SODA"
    WEBCAM = "Webcam"

class LightControllerType(Enum):
    DCP = "DCPController"
    LCP = "LCPController"
