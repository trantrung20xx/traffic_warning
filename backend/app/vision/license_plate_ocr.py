from __future__ import annotations

from dataclasses import dataclass
import os
import threading
import time
from multiprocessing import get_context
from queue import Empty, Full
from typing import Callable, Optional

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

        # Định dạng phổ biến ở PaddleOCR v2: [[box, (text, conf)], ...]
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
            # Định dạng phổ biến ở chế độ rec-only: [("TEXT", conf), ...]
            if len(current) >= 2 and isinstance(current[0], str):
                confidence = _to_confidence(current[1])
                if confidence is not None:
                    text = str(current[0]).strip()
                    if text:
                        yield text, confidence
                continue
            stack.extend(reversed(current))
            continue

        # Định dạng mới có thể trả dict chứa rec_texts / rec_scores.
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


def _build_paddle_init_candidates(
    *,
    ocr_version: str,
    text_detection_model_name: str,
    text_recognition_model_name: str,
    lang: str,
    use_gpu: bool,
) -> list[dict]:
    return [
        {
            "text_detection_model_name": text_detection_model_name,
            "text_recognition_model_name": text_recognition_model_name,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_gpu": use_gpu,
        },
        {
            "lang": lang,
            "ocr_version": ocr_version,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_gpu": use_gpu,
        },
        {
            "use_angle_cls": False,
            "lang": lang,
            "show_log": False,
            "use_gpu": use_gpu,
            "ocr_version": ocr_version,
            "text_detection_model_name": text_detection_model_name,
            "text_recognition_model_name": text_recognition_model_name,
        },
        {
            "use_angle_cls": False,
            "lang": lang,
            "show_log": False,
            "use_gpu": use_gpu,
            "ocr_version": ocr_version,
            "text_detection_model_name": text_detection_model_name,
        },
        {
            "use_angle_cls": False,
            "lang": lang,
            "show_log": False,
            "use_gpu": use_gpu,
        },
        {
            "use_angle_cls": False,
            "lang": lang,
            "show_log": False,
        },
        {
            "use_angle_cls": False,
            "lang": lang,
        },
    ]


def _paddle_ocr_worker_main(request_queue, response_queue, config: dict) -> None:
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

    try:
        from paddleocr import PaddleOCR
        import paddle  # noqa: F401
        import numpy as np
    except Exception as exc:
        response_queue.put(
            {
                "type": "startup",
                "ok": False,
                "error": f"failed to import paddle runtime: {exc}",
            }
        )
        return

    init_candidates = _build_paddle_init_candidates(
        ocr_version=str(config.get("ocr_version") or "PP-OCRv5"),
        text_detection_model_name=str(config.get("text_detection_model_name") or "PP-OCRv5_mobile_det"),
        text_recognition_model_name=str(config.get("text_recognition_model_name") or "PP-OCRv5_mobile_rec"),
        lang=str(config.get("lang") or "en").strip().lower(),
        use_gpu=bool(config.get("use_gpu", False)),
    )

    engine = None
    last_error: Optional[Exception] = None
    for kwargs in init_candidates:
        try:
            engine = PaddleOCR(**kwargs)
            break
        except (TypeError, ValueError):
            continue
        except Exception as exc:
            last_error = exc
            continue

    if engine is None:
        error_text = str(last_error) if last_error is not None else "unsupported PaddleOCR signature"
        response_queue.put({"type": "startup", "ok": False, "error": error_text})
        return

    response_queue.put({"type": "startup", "ok": True})

    while True:
        message = request_queue.get()
        if not message:
            continue
        if message.get("type") == "shutdown":
            break

        request_id = int(message.get("request_id", -1))
        image_jpg = message.get("image_jpg")
        if request_id < 0 or not image_jpg:
            response_queue.put(
                {
                    "type": "result",
                    "request_id": request_id,
                    "ok": True,
                    "text": None,
                    "confidence": None,
                }
            )
            continue

        try:
            image_bytes = bytes(image_jpg)
            buffer = np.frombuffer(image_bytes, dtype=np.uint8)
            image_bgr = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            if image_bgr is None:
                response_queue.put(
                    {
                        "type": "result",
                        "request_id": request_id,
                        "ok": True,
                        "text": None,
                        "confidence": None,
                    }
                )
                continue

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            payload = engine.ocr(image_rgb, cls=False)
            best = _extract_best_from_paddle_payload(payload)
            if best is None:
                response_queue.put(
                    {
                        "type": "result",
                        "request_id": request_id,
                        "ok": True,
                        "text": None,
                        "confidence": None,
                    }
                )
                continue

            response_queue.put(
                {
                    "type": "result",
                    "request_id": request_id,
                    "ok": True,
                    "text": best.text,
                    "confidence": best.confidence,
                }
            )
        except Exception as exc:
            response_queue.put(
                {
                    "type": "result",
                    "request_id": request_id,
                    "ok": False,
                    "error": str(exc),
                }
            )


class PaddleOcrSubprocessClient:
    """
    Tách PaddleOCR sang process riêng để tránh xung đột runtime CUDA với PyTorch.
    """

    def __init__(
        self,
        *,
        ocr_version: str = "PP-OCRv5",
        text_detection_model_name: str = "PP-OCRv5_mobile_det",
        text_recognition_model_name: str = "PP-OCRv5_mobile_rec",
        lang: str = "en",
        use_gpu: bool = False,
        startup_timeout_s: float = 30.0,
        request_timeout_ms: int = 1200,
        request_jpeg_quality: int = 92,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.available = False
        self._on_log = on_log or (lambda _: None)
        self._startup_timeout_s = max(float(startup_timeout_s), 1.0)
        self._request_timeout_ms = max(int(request_timeout_ms), 100)
        self._request_jpeg_quality = min(max(int(request_jpeg_quality), 40), 100)
        self._request_lock = threading.Lock()
        self._request_seq = 0
        self._request_queue = None
        self._response_queue = None
        self._process = None
        self._config = {
            "ocr_version": str(ocr_version or "PP-OCRv5"),
            "text_detection_model_name": str(text_detection_model_name or "PP-OCRv5_mobile_det"),
            "text_recognition_model_name": str(text_recognition_model_name or "PP-OCRv5_mobile_rec"),
            "lang": str(lang or "en").strip().lower(),
            "use_gpu": bool(use_gpu),
        }
        self._start_worker()

    def _start_worker(self) -> None:
        try:
            context = get_context("spawn")
            self._request_queue = context.Queue(maxsize=64)
            self._response_queue = context.Queue(maxsize=128)
            self._process = context.Process(
                target=_paddle_ocr_worker_main,
                args=(self._request_queue, self._response_queue, self._config),
                daemon=True,
            )
            self._process.start()
        except Exception as exc:
            self._on_log(f"[license_plate_ocr] failed to start paddle subprocess: {exc}")
            self.close()
            return

        deadline = time.time() + self._startup_timeout_s
        while time.time() < deadline:
            remaining = max(deadline - time.time(), 0.1)
            try:
                message = self._response_queue.get(timeout=remaining)
            except Empty:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("type") != "startup":
                continue
            if bool(message.get("ok")):
                self.available = True
                return
            self._on_log(
                f"[license_plate_ocr] paddle subprocess startup failed: {message.get('error')}"
            )
            self.close()
            return

        self._on_log("[license_plate_ocr] paddle subprocess startup timeout.")
        self.close()

    def read_best(self, image_bgr) -> Optional[OcrReadout]:
        if not self.available or self._request_queue is None or self._response_queue is None:
            return None
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return None

        ok, encoded = cv2.imencode(
            ".jpg",
            image_bgr,
            [int(cv2.IMWRITE_JPEG_QUALITY), self._request_jpeg_quality],
        )
        if not ok:
            return None

        with self._request_lock:
            self._request_seq += 1
            request_id = int(self._request_seq)
            try:
                self._request_queue.put(
                    {
                        "type": "read_best",
                        "request_id": request_id,
                        "image_jpg": encoded.tobytes(),
                    },
                    timeout=0.5,
                )
            except Full:
                self._on_log("[license_plate_ocr] paddle subprocess request queue is full.")
                return None

            deadline = time.time() + (self._request_timeout_ms / 1000.0)
            while time.time() < deadline:
                remaining = max(deadline - time.time(), 0.05)
                try:
                    message = self._response_queue.get(timeout=remaining)
                except Empty:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") != "result":
                    continue
                if int(message.get("request_id", -1)) != request_id:
                    continue
                if not bool(message.get("ok", False)):
                    self._on_log(
                        f"[license_plate_ocr] paddle subprocess inference failed: {message.get('error')}"
                    )
                    return None
                text = message.get("text")
                confidence = _to_confidence(message.get("confidence"))
                if not text or confidence is None:
                    return None
                return OcrReadout(text=str(text), confidence=confidence)

            self._on_log(
                f"[license_plate_ocr] paddle subprocess timeout after {self._request_timeout_ms} ms."
            )
            return None

    def close(self) -> None:
        self.available = False
        try:
            if self._request_queue is not None:
                self._request_queue.put({"type": "shutdown"}, timeout=0.2)
        except Exception:
            pass

        if self._process is not None:
            try:
                self._process.join(timeout=1.5)
            except Exception:
                pass
            if self._process.is_alive():
                try:
                    self._process.terminate()
                except Exception:
                    pass
                try:
                    self._process.join(timeout=0.5)
                except Exception:
                    pass

        self._process = None
        self._request_queue = None
        self._response_queue = None


class LicensePlateOcr:
    """
    Bộ OCR biển số với backend có thể thay thế (easyocr hoặc paddleocr).
    """

    def __init__(
        self,
        *,
        backend: str = "paddleocr",
        paddle_ocr_version: str = "PP-OCRv5",
        paddle_text_detection_model_name: str = "PP-OCRv5_mobile_det",
        paddle_text_recognition_model_name: str = "PP-OCRv5_mobile_rec",
        paddle_lang: str = "en",
        paddle_use_gpu: bool = False,
        on_log: Optional[Callable[[str], None]] = None,
    ):
        self.backend = str(backend or "paddleocr").strip().lower()
        self._paddle_ocr_version = str(paddle_ocr_version or "PP-OCRv5").strip()
        self._paddle_text_detection_model_name = str(
            paddle_text_detection_model_name or "PP-OCRv5_mobile_det"
        ).strip()
        self._paddle_text_recognition_model_name = str(
            paddle_text_recognition_model_name or "PP-OCRv5_mobile_rec"
        ).strip()
        self._paddle_lang = str(paddle_lang or "en").strip().lower()
        self._paddle_use_gpu = bool(paddle_use_gpu)
        self._on_log = on_log or (lambda _: None)
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
        self._on_log(f"[license_plate_ocr] unsupported backend={self.backend}. OCR disabled.")

    def _init_easyocr(self) -> None:
        try:
            import easyocr
        except Exception as exc:
            self._on_log(f"[license_plate_ocr] easyocr is unavailable: {exc}. OCR disabled.")
            return
        try:
            self._engine = easyocr.Reader(["en"], gpu=False, verbose=False)
            self.available = True
        except Exception as exc:
            self._on_log(f"[license_plate_ocr] failed to initialize easyocr: {exc}. OCR disabled.")

    def _init_paddleocr(self) -> None:
        try:
            from paddleocr import PaddleOCR
        except Exception as exc:
            self._on_log(f"[license_plate_ocr] paddleocr is unavailable: {exc}. OCR disabled.")
            return

        try:
            import paddle  # noqa: F401
        except Exception as exc:
            self._on_log(
                f"[license_plate_ocr] paddleocr backend requires paddlepaddle runtime: {exc}. OCR disabled."
            )
            return

        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        init_candidates = _build_paddle_init_candidates(
            ocr_version=self._paddle_ocr_version,
            text_detection_model_name=self._paddle_text_detection_model_name,
            text_recognition_model_name=self._paddle_text_recognition_model_name,
            lang=self._paddle_lang,
            use_gpu=self._paddle_use_gpu,
        )

        last_error: Optional[Exception] = None
        for kwargs in init_candidates:
            try:
                self._engine = PaddleOCR(**kwargs)
                self.available = True
                return
            except (TypeError, ValueError):
                continue
            except Exception as exc:
                last_error = exc
                continue

        if last_error is not None:
            self._on_log(f"[license_plate_ocr] failed to initialize paddleocr: {last_error}. OCR disabled.")
            return
        self._on_log("[license_plate_ocr] failed to initialize paddleocr: unsupported signature.")

    def read_best(self, image_bgr) -> Optional[OcrReadout]:
        if not self.available or self._engine is None:
            return None
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return None
        try:
            rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            return None

        try:
            if self.backend == "easyocr":
                return self._read_easyocr(rgb)
            if self.backend == "paddleocr":
                return self._read_paddleocr(rgb)
        except Exception as exc:
            self._on_log(f"[license_plate_ocr] OCR inference failed: {exc}")
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
            confidence = float(item[2])
            if not text:
                continue
            if confidence > best_conf:
                best_text = text
                best_conf = confidence

        if best_text is None or best_conf < 0.0:
            return None
        return OcrReadout(text=best_text, confidence=min(max(best_conf, 0.0), 1.0))

    def _read_paddleocr(self, image_rgb) -> Optional[OcrReadout]:
        results = self._engine.ocr(image_rgb, cls=False)
        if not results:
            return None
        return _extract_best_from_paddle_payload(results)
