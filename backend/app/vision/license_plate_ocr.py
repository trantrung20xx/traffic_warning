from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Optional

import cv2


@dataclass(frozen=True)
class OcrReadout:
    # Kết quả OCR đã chuẩn hóa về [text, confidence].
    text: str
    confidence: float


def _to_confidence(value) -> Optional[float]:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0.0:
        return None
    # Chuẩn hóa confidence về [0,1] để downstream xử lý đồng nhất.
    return min(max(confidence, 0.0), 1.0)


def _iter_paddle_readouts(payload):
    # PaddleOCR trả payload khác nhau giữa version; duyệt đệ quy để gom mọi định dạng.
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
                            # Format phổ biến: [bbox, [text, conf], ...]
                            yield text, confidence
                    continue
            if len(current) >= 2 and isinstance(current[0], str):
                confidence = _to_confidence(current[1])
                if confidence is not None:
                    text = str(current[0]).strip()
                    if text:
                        # Format rút gọn: [text, conf]
                        yield text, confidence
                continue
            # Duyệt sâu theo stack để tương thích payload lồng nhau nhiều tầng.
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
        # Chọn kết quả confidence cao nhất làm đại diện cho frame hiện tại.
        if confidence > best_conf:
            best_text = text
            best_conf = confidence
    if best_text is None or best_conf < 0.0:
        return None
    return OcrReadout(text=best_text, confidence=min(max(best_conf, 0.0), 1.0))


def _run_paddleocr_inference(engine, image_rgb):
    predict = getattr(engine, "predict", None)
    if callable(predict):
        # PaddleOCR 3.x thường expose predict().
        return predict(image_rgb)

    ocr = getattr(engine, "ocr", None)
    if not callable(ocr):
        raise RuntimeError("PaddleOCR engine exposes neither predict() nor ocr().")

    # PaddleOCR 2.x cần cls=False; PaddleOCR 3.x lại lỗi với tham số này.
    # Cơ chế retry giúp tương thích cả hai.
    try:
        return ocr(image_rgb, cls=False)
    except TypeError as exc:
        message = str(exc)
        if "cls" not in message or "unexpected keyword" not in message:
            raise
        # PaddleOCR version mới bỏ tham số cls, retry không kèm cls.
        return ocr(image_rgb)


def _create_paddle_ocr_engine(
    *,
    ocr_version: str,
    text_detection_model_name: str,
    text_recognition_model_name: str,
    lang: str,
    use_gpu: bool,
):
    # Tắt model source check để tránh lỗi môi trường khi khởi tạo engine.
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
        # Khi không chỉ định model cụ thể thì dùng profile theo lang + phiên bản OCR.
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
        self._backend_reader: Optional[Callable] = None
        # available phản ánh backend đã init thành công và sẵn sàng nhận ảnh.
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
        # Cho phép nhập nhiều lang bằng dấu phẩy/chấm phẩy/khoảng trắng.
        raw = self._easyocr_lang.replace(";", ",").replace(" ", ",")
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if not items:
            return ["en"]
        # Dùng dict.fromkeys để giữ thứ tự và loại trùng.
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
            self._backend_reader = self._read_easyocr
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
            self._backend_reader = self._read_paddleocr
            self.available = True
        except Exception:
            return

    def read_best(self, image_bgr, *, aggressive: bool = False) -> Optional[OcrReadout]:
        if not self.available or self._engine is None:
            return None
        if image_bgr is None or getattr(image_bgr, "size", 0) == 0:
            return None
        primary = self._read_backend_from_bgr(image_bgr)
        if primary is not None and float(primary.confidence) >= 0.82:
            return primary
        if not aggressive:
            return primary

        best = primary
        for variant_bgr in self._generate_ocr_variants(image_bgr):
            candidate = self._read_backend_from_bgr(variant_bgr)
            if candidate is None:
                continue
            if best is None or float(candidate.confidence) > float(best.confidence):
                best = candidate
            if best is not None and float(best.confidence) >= 0.90:
                break
        return best

    def _read_backend_from_bgr(self, image_bgr) -> Optional[OcrReadout]:
        try:
            # Chuẩn hóa input sang RGB vì đa số OCR backend kỳ vọng RGB.
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        except Exception:
            return None

        try:
            return self._backend_reader(image_rgb)
        except Exception:
            return None

    def _generate_ocr_variants(self, image_bgr) -> list:
        variants: list = []
        try:
            height, width = image_bgr.shape[:2]
        except Exception:
            return variants
        if height <= 0 or width <= 0:
            return variants

        def _append_variant(candidate) -> None:
            if candidate is None or getattr(candidate, "size", 0) == 0:
                return
            variants.append(candidate)

        enhanced_source = image_bgr
        if height < 72 or width < 240:
            scale = max(2.0, 72.0 / float(height), 240.0 / float(width))
            scale = min(scale, 4.0)
            scaled_w = max(int(round(width * scale)), 1)
            scaled_h = max(int(round(height * scale)), 1)
            try:
                enhanced_source = cv2.resize(
                    image_bgr,
                    (scaled_w, scaled_h),
                    interpolation=cv2.INTER_CUBIC,
                )
                _append_variant(enhanced_source)
            except Exception:
                enhanced_source = image_bgr

        try:
            h2, w2 = enhanced_source.shape[:2]
            pad_x = max(int(round(w2 * 0.08)), 4)
            pad_y = max(int(round(h2 * 0.18)), 4)
            _append_variant(
                cv2.copyMakeBorder(
                    enhanced_source,
                    pad_y,
                    pad_y,
                    pad_x,
                    pad_x,
                    cv2.BORDER_REPLICATE,
                )
            )
        except Exception:
            pass

        try:
            gray = cv2.cvtColor(enhanced_source, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
            _append_variant(cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR))

            blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
            sharpened = cv2.addWeighted(clahe, 1.7, blur, -0.7, 0)
            _append_variant(cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR))

            denoised = cv2.bilateralFilter(sharpened, 5, 35, 35)
            threshold = cv2.adaptiveThreshold(
                denoised,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                8,
            )
            _append_variant(cv2.cvtColor(threshold, cv2.COLOR_GRAY2BGR))

            _, otsu = cv2.threshold(
                denoised,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )
            _append_variant(cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR))
        except Exception:
            pass
        return variants

    def _read_easyocr(self, image_rgb) -> Optional[OcrReadout]:
        try:
            results = self._engine.readtext(
                image_rgb,
                detail=1,
                paragraph=False,
                allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            )
        except TypeError:
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
            # Lấy candidate có confidence cao nhất.
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
