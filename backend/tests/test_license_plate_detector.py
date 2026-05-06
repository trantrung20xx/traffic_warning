from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("ultralytics", SimpleNamespace(YOLO=object))

from app.vision.license_plate_detector import YoloV8LicensePlateDetector


class _FakeTensor:
    def __init__(self, values):
        self._values = np.array(values)

    def cpu(self):
        return self

    def numpy(self):
        return self._values


class _FakeBoxes:
    xyxy = _FakeTensor(
        [
            [0.0, 0.0, 100.0, 60.0],
            [10.0, 20.0, 45.0, 35.0],
        ]
    )
    conf = _FakeTensor([0.95, 0.70])
    cls = _FakeTensor([1, 0])


class _FakeResult:
    boxes = _FakeBoxes()


class _FakeModel:
    names = {0: "License Plates", 1: "Vehicles"}

    def predict(self, *args, **kwargs):
        return [_FakeResult()]


def test_license_plate_detector_filters_allowed_classes() -> None:
    detector = object.__new__(YoloV8LicensePlateDetector)
    detector.model = _FakeModel()
    detector.conf_threshold = 0.35
    detector.iou_threshold = 0.7
    detector.device = "cpu"
    detector.class_names = dict(detector.model.names)
    detector.allowed_classes = detector._normalize_allowed_classes(["License Plates"])
    detector.allowed_class_set = {
        detector._normalize_class_name(item) for item in detector.allowed_classes
    }
    detector.allowed_class_ids = [
        cls_id
        for cls_id, name in detector.class_names.items()
        if detector._normalize_class_name(name) in detector.allowed_class_set
    ]

    detections = detector.detect(np.zeros((80, 120, 3), dtype=np.uint8))

    assert len(detections) == 1
    assert detections[0].bbox_xyxy == [10.0, 20.0, 45.0, 35.0]
    assert detections[0].confidence == 0.70
