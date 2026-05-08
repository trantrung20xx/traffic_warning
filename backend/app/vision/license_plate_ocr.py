from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

import cv2


@dataclass(frozen=True)
class OcrReadout:
    text: str
    confidence: float


def _to_confidence(value) -> Optional[float]:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0:
        return None
    return min(max(confidence, 0.0), 1.0)


def _iter_paddle_readouts(payload):
    stack = [payload]
    while stack:
        current = stack.pop()
        if current is None:
            continue

        if isinstance(current, (list, tuple)):
            if len(current) >= 2:
                text_conf = current[1]
                if (
                    isinstance(text_conf, (list, tuple))
                    and len(text_conf) >= 2
                    and isinstance(text_conf[0], str)
                ):
                    confidence = _to_confidence(text_conf[1])
                    if confidence is not None:
                        text = str(text_conf[0]).strip()
                        if text:
                            yield text, confidence
                    continue
            if len(current) >= 2 and isinstance(current[0], str):
                confidence = _to_confidence(current[1])
                if confidence is not None:
                    text = str(current[0]).strip()
                    if text:
                        yield text, confidence
                continue
            stack.extend(reversed(current))
            continue

        if isinstance(current, dict):
            rec_texts = current.get("rec_texts")
            rec_scores = current.get("rec_scores")
            if isinstance(rec_texts, list) and isinstance(rec_scores, list):
                for text, score in zip(rec_texts, rec_scores):
                    confidence = _to_confidence(score)
                    if confidence is None:
                        continue
                    text_value = str(text).strip()
                    if text_value:
                        yield text_value, confidence
            for value in current.values():
                stack.append(value)


def _extract_best_from_paddle_payload(payload) -> Optional[OcrReadout]:
    best_text: Optional[str] = None
    best_conf: float = -1.0
    for text, confidence in _iter_paddle_readouts(payload):
        if confidence > best_conf:
            best_text = text
            best_conf = confidence
    if best_text is None or best_conf < 0.0:
        return None
    return OcrReadout(text=best_text, confidence=min(max(best_conf, 0.0), 1.0))


def _run_paddleocr_inference(engine, image_rgb):
    predict = getattr(engine, "predict", None)
    if callable(predict):
        return predict(image_rgb)

    ocr = getattr(engine, "ocr", None)
    if not callable(ocr):
        raise RuntimeError("PaddleOCR engine exposes neither predict() nor ocr().")

    # PaddleOCR 2.x uses cls=False; PaddleOCR 3.x rejects that legacy keyword.
    try:
        return ocr(image_rgb, cls=False)
    except TypeError as exc:
        message = str(exc)
        if "cls" not in message or "unexpected keyword" not in message:
            raise
        return ocr(image_rgb)


def _create_paddle_ocr_engine(
    *,
    ocr_version: str,
    text_detection_model_name: str,
    text_recognition_model_name: str,
    lang: str,
    use_gpu: bool,
):
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR

    device = "gpu:0" if bool(use_gpu) else "cpu"
    kwargs = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "device": device,
    }
    det_model = str(text_detection_model_name or "").strip()
    rec_model = str(text_recognition_model_name or "").strip()
    if det_model:
        kwargs["text_detection_model_name"] = det_model
    if rec_model:
        kwargs["text_recognition_model_name"] = rec_model
    if not det_model and not rec_model:
        kwargs["lang"] = str(lang or "en").strip().lower()
        kwargs["ocr_version"] = str(ocr_version or "PP-OCRv5").strip()
    return PaddleOCR(**kwargs)


class LicensePlateOcr:
    """
    OCR biển số chạy in-process (easyocr hoặc paddleocr).
    """

    def __init__(
        self,
        *,
        backend: str = "paddleocr",
        easyocr_lang: str = "en",
        easyocr_use_gpu: bool = False,
        paddle_ocr_version: str = "PP-OCRv5",
        paddle_text_detection_model_name: str = "PP-OCRv5_mobile_det",
        paddle_text_recognition_model_name: str = "PP-OCRv5_mobile_rec",
        paddle_lang: str = "en",
        paddle_use_gpu: bool = False,
    ):
        self.backend = str(backend or "paddleocr").strip().lower()
        self._easyocr_lang = str(easyocr_lang or "en").strip().lower()
        self._easyocr_use_gpu = bool(easyocr_use_gpu)
        self._paddle_ocr_version = str(paddle_ocr_version or "PP-OCRv5").strip()
        self._paddle_text_detection_model_name = str(
            paddle_text_detection_model_name or "PP-OCRv5_mobile_det"
        ).strip()
        self._paddle_text_recognition_model_name = str(
            paddle_text_recognition_model_name or "PP-OCRv5_mobile_rec"
        ).strip()
        self._paddle_lang = str(paddle_lang or "en").strip().lower()
        self._paddle_use_gpu = bool(paddle_use_gpu)
        self._engine = None
        self.available = False
        self._init_backend()

    def _init_backend(self) -> None:
        if self.backend == "easyocr":
            self._init_easyocr()
            return
        if self.backend == "paddleocr":
            self._init_paddleocr()
            return

    def _easyocr_languages(self) -> list[str]:
        raw = self._easyocr_lang.replace(";", ",").replace(" ", ",")
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if not items:
            return ["en"]
        return list(dict.fromkeys(items))

    def _init_easyocr(self) -> None:
        try:
            import easyocr
        except Exception:
            return
        try:
            self._engine = easyocr.Reader(
                self._easyocr_languages(),
                gpu=self._easyocr_use_gpu,
                verbose=False,
            )
            self.available = True
        except Exception:
            return

    def _init_paddleocr(self) -> None:
        try:
            self._engine = _create_paddle_ocr_engine(
                ocr_version=self._paddle_ocr_version,
                text_detection_model_name=self._paddle_text_detection_model_name,
                text_recognition_model_name=self._paddle_text_recognition_model_name,
                lang=self._paddle_lang,
                use_gpu=self._paddle_use_gpu,
            )
            self.available = True
        except Exception:
            return

    def read_best(self, image_bgr) -> Optional[OcrReadout]:
        if not self.available or self._engine is None:
            return None
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return None
        try:
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            return None

        try:
            if self.backend == "easyocr":
                return self._read_easyocr(image_rgb)
            if self.backend == "paddleocr":
                return self._read_paddleocr(image_rgb)
        except Exception:
            return None
        return None

    def _read_easyocr(self, image_rgb) -> Optional[OcrReadout]:
        results = self._engine.readtext(image_rgb, detail=1, paragraph=False)
        if not results:
            return None

        best_text: Optional[str] = None
        best_conf: float = -1.0
        for item in results:
            if len(item) < 3:
                continue
            text = str(item[1]).strip()
            confidence = _to_confidence(item[2])
            if not text or confidence is None:
                continue
            if confidence > best_conf:
                best_text = text
                best_conf = confidence

        if best_text is None or best_conf < 0.0:
            return None
        return OcrReadout(text=best_text, confidence=min(max(best_conf, 0.0), 1.0))

    def _read_paddleocr(self, image_rgb) -> Optional[OcrReadout]:
        results = _run_paddleocr_inference(self._engine, image_rgb)
        if not results:
            return None
        return _extract_best_from_paddle_payload(results)
