from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vision.license_plate_ocr import (
    _create_paddle_ocr_engine,
    _run_paddleocr_inference,
)


class _PaddleOcrV3Engine:
    def __init__(self) -> None:
        self.predict_calls = 0
        self.ocr_calls = 0

    def predict(self, image):
        self.predict_calls += 1
        return [{"rec_texts": ["51A12345"], "rec_scores": [0.91]}]

    def ocr(self, image, **kwargs):
        self.ocr_calls += 1
        raise AssertionError("PaddleOCR 3.x inference should use predict().")


class _PaddleOcrV2Engine:
    def __init__(self) -> None:
        self.cls_values: list[bool] = []

    def ocr(self, image, cls=True):
        self.cls_values.append(cls)
        return [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("30H9999", 0.84)]]


class _PaddleOcrNoClsEngine:
    def __init__(self) -> None:
        self.calls = 0

    def ocr(self, image, **kwargs):
        self.calls += 1
        if "cls" in kwargs:
            raise TypeError("PaddleOCR.predict() got an unexpected keyword argument 'cls'")
        return [[("59C12345", 0.77)]]


def test_paddleocr_v3_inference_uses_predict_without_cls() -> None:
    engine = _PaddleOcrV3Engine()
    payload = _run_paddleocr_inference(engine, np.zeros((10, 20, 3), dtype=np.uint8))

    assert payload == [{"rec_texts": ["51A12345"], "rec_scores": [0.91]}]
    assert engine.predict_calls == 1
    assert engine.ocr_calls == 0


def test_paddleocr_v2_inference_keeps_legacy_cls_disabled() -> None:
    engine = _PaddleOcrV2Engine()
    payload = _run_paddleocr_inference(engine, np.zeros((10, 20, 3), dtype=np.uint8))

    assert payload == [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("30H9999", 0.84)]]
    assert engine.cls_values == [False]


def test_paddleocr_inference_retries_ocr_without_unsupported_cls() -> None:
    engine = _PaddleOcrNoClsEngine()
    payload = _run_paddleocr_inference(engine, np.zeros((10, 20, 3), dtype=np.uint8))

    assert payload == [[("59C12345", 0.77)]]
    assert engine.calls == 2


def test_paddleocr_engine_uses_default_mkldnn_for_cpu(monkeypatch) -> None:
    captured_kwargs = {}

    class _FakePaddleOCR:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=_FakePaddleOCR))
    monkeypatch.delenv("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", raising=False)

    _create_paddle_ocr_engine(
        ocr_version="PP-OCRv5",
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        lang="en",
        use_gpu=False,
    )

    assert os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] == "True"
    assert captured_kwargs["device"] == "cpu"
    assert "enable_mkldnn" not in captured_kwargs
