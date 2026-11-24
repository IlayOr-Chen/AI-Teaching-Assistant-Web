"""
Purpose:
    Implements `PhotoHeadCounter` for Feature 1.5 (Automatic Classroom Student
    Headcount via Local Ultralytics YOLO). Detects physical students (`person`
    class only), generates an annotated bounding-box image, compares the YOLO
    count against self-registered attendance, and computes a GREEN / ORANGE / RED
    confidence indicator.
Inputs:
    `FirebaseRepository` persistence instance, `AppConfig` runtime settings, and
    classroom photo files or byte streams (tested in real life on `/assets/p3.jpeg`).
Outputs:
    `PhotoHeadCounter` class producing detection metrics, annotated image Data URLs,
    and color-coded confidence assessments.
"""

from __future__ import annotations

import base64
import io
import os
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from src.config import AppConfig
from src.repository.firebase_repository import FirebaseRepository

os.environ.setdefault("YOLO_AUTOINSTALL", "False")

PERSON_CLASS_ID = 0
HIGH_CONF_BOX_THRESHOLD = 0.60
LOW_CONF_BOX_THRESHOLD = 0.45


class PhotoHeadCounter:
    """
    Purpose:
        Executes local Ultralytics YOLO person detection on classroom photos,
        renders bounding boxes with confidence scores, persists headcount metadata
        in Firebase RTDB, and determines the GREEN / ORANGE / RED status indicator.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB repository.
        config (AppConfig): Configuration holding `yolo_model_path` and `yolo_confidence_threshold`.
        yolo_model (Any | None): Optional pre-loaded or mock YOLO model instance.
        imgsz (int): Inference image resolution (default `1280` for wide classroom halls).
    Outputs:
        `PhotoHeadCounter` service instance.
    """

    _SHARED_MODELS: dict[str, Any] = {}

    def __init__(
        self,
        repository: FirebaseRepository,
        config: AppConfig,
        yolo_model: Any | None = None,
        imgsz: int = 1280,
    ) -> None:
        """
        Purpose:
            Initialize `PhotoHeadCounter` with repository, configuration, and YOLO parameters.
        Inputs:
            repository (FirebaseRepository): Target Firebase RTDB repository.
            config (AppConfig): Application configuration.
            yolo_model (Any | None): Optional injected YOLO model for testing.
            imgsz (int): Inference resolution (1280 captures small back-row students accurately).
        Outputs:
            None.
        """
        self.repository = repository
        self.config = config
        self._model = yolo_model
        self.imgsz = imgsz

    def _get_model(self) -> Any:
        """
        Purpose:
            Lazily load and cache the local Ultralytics YOLO model weights across
            instances to avoid redundant PyTorch model deserialization overhead.
        Inputs:
            None.
        Outputs:
            Any: Initialized `ultralytics.YOLO` model instance.
        """
        if self._model is None:
            model_key = self.config.yolo_model_path
            if model_key not in self._SHARED_MODELS:
                from ultralytics import YOLO

                self._SHARED_MODELS[model_key] = YOLO(model_key)
            self._model = self._SHARED_MODELS[model_key]
        return self._model

    @staticmethod
    def _load_pil_image(image_source: str | Path | bytes) -> Image.Image:
        """
        Purpose:
            Validate and decode an image from disk path or raw bytes into an RGB `PIL.Image`.
        Inputs:
            image_source (str | Path | bytes): File path or raw image bytes.
        Outputs:
            Image.Image: Decoded RGB PIL Image. Raises `FileNotFoundError` or `ValueError` on failure.
        """
        try:
            if isinstance(image_source, (bytes, bytearray)):
                if not image_source:
                    raise ValueError("Uploaded image payload is empty.")
                img = Image.open(io.BytesIO(image_source))
            else:
                path_obj = Path(image_source)
                if not path_obj.is_file():
                    raise FileNotFoundError(f"Classroom photo not found: {path_obj}")
                img = Image.open(path_obj)
            img.load()
            return img.convert("RGB")
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Invalid classroom image file. Please upload a valid JPEG or PNG photo: {exc}"
            ) from exc

    @staticmethod
    def evaluate_confidence_indicator(
        detected_count: int,
        confidences: list[float],
        registered_count: int | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Compute the Lecturer's Color-Coded Confidence Indicator (`GREEN`, `ORANGE`, `RED`)
            based on YOLO bounding-box detection confidence scores and the count discrepancy
            against self-registered student attendance.
        Inputs:
            detected_count (int): Number of physical `person` instances detected by YOLO.
            confidences (list[float]): Confidence score (`0.0`..`1.0`) for each person box.
            registered_count (int | None): Self-registered QR/manual attendance count for comparison.
        Outputs:
            dict[str, Any]: Dictionary containing `status_color` (`GREEN` | `ORANGE` | `RED`),
            `status_label`, `status_reason`, `average_confidence`, `discrepancy`, and box counts.
        """
        avg_conf = (
            round(sum(confidences) / len(confidences), 3) if confidences else 0.0
        )
        min_conf = round(min(confidences), 3) if confidences else 0.0
        max_conf = round(max(confidences), 3) if confidences else 0.0
        high_conf_count = sum(1 for c in confidences if c >= HIGH_CONF_BOX_THRESHOLD)
        low_conf_count = sum(1 for c in confidences if c < LOW_CONF_BOX_THRESHOLD)
        low_conf_ratio = (
            (low_conf_count / detected_count) if detected_count > 0 else 0.0
        )

        discrepancy: int | None = None
        abs_discrepancy: int | None = None
        if registered_count is not None:
            discrepancy = detected_count - int(registered_count)
            abs_discrepancy = abs(discrepancy)

        # Rule 1 (RED): Zero detections when students registered, very low avg confidence (<0.45),
        # or large discrepancy (>= 4 students difference) between physical count and QR check-ins
        if detected_count == 0 and (registered_count is None or registered_count > 0):
            return {
                "status_color": "RED",
                "status_label": "Low Confidence / No Persons Detected",
                "status_reason": (
                    "YOLO detected 0 persons in the uploaded photo. Please verify the image "
                    "angle/lighting and review attendance manually."
                ),
                "average_confidence": avg_conf,
                "min_confidence": min_conf,
                "max_confidence": max_conf,
                "high_confidence_count": high_conf_count,
                "low_confidence_count": low_conf_count,
                "discrepancy": discrepancy,
                "abs_discrepancy": abs_discrepancy,
            }

        if abs_discrepancy is not None and abs_discrepancy >= 4:
            return {
                "status_color": "RED",
                "status_label": "Large Attendance Discrepancy — Manual Review Required",
                "status_reason": (
                    f"Large mismatch of {abs_discrepancy} students between YOLO headcount "
                    f"({detected_count}) and self-registered attendance ({registered_count})."
                ),
                "average_confidence": avg_conf,
                "min_confidence": min_conf,
                "max_confidence": max_conf,
                "high_confidence_count": high_conf_count,
                "low_confidence_count": low_conf_count,
                "discrepancy": discrepancy,
                "abs_discrepancy": abs_discrepancy,
            }

        if detected_count > 0 and avg_conf < 0.45:
            return {
                "status_color": "RED",
                "status_label": "Low Detection Confidence — Manual Review Required",
                "status_reason": (
                    f"Average YOLO person confidence ({avg_conf:.2f}) is below the reliable "
                    f"threshold (0.45) due to heavy occlusion or poor lighting."
                ),
                "average_confidence": avg_conf,
                "min_confidence": min_conf,
                "max_confidence": max_conf,
                "high_confidence_count": high_conf_count,
                "low_confidence_count": low_conf_count,
                "discrepancy": discrepancy,
                "abs_discrepancy": abs_discrepancy,
            }

        # Rule 2 (ORANGE): Moderate discrepancy (2-3 students), moderate average confidence
        # (0.45 <= avg_conf < 0.60), or >25% of detected boxes are low-confidence back-row detections
        if abs_discrepancy is not None and 2 <= abs_discrepancy <= 3:
            return {
                "status_color": "ORANGE",
                "status_label": "Moderate Count Discrepancy — Visual Warning",
                "status_reason": (
                    f"Small discrepancy of {abs_discrepancy} students between YOLO headcount "
                    f"({detected_count}) and self-registered attendance ({registered_count}), "
                    f"with {low_conf_count} low-confidence/occluded detections."
                ),
                "average_confidence": avg_conf,
                "min_confidence": min_conf,
                "max_confidence": max_conf,
                "high_confidence_count": high_conf_count,
                "low_confidence_count": low_conf_count,
                "discrepancy": discrepancy,
                "abs_discrepancy": abs_discrepancy,
            }

        if avg_conf < 0.60 or low_conf_ratio > 0.25:
            return {
                "status_color": "ORANGE",
                "status_label": "Moderate Uncertainty (Back-Row Occlusion Warning)",
                "status_reason": (
                    f"Detected {detected_count} persons (avg confidence {avg_conf:.2f}), including "
                    f"{low_conf_count} partially occluded/distant detections below 0.45 confidence."
                ),
                "average_confidence": avg_conf,
                "min_confidence": min_conf,
                "max_confidence": max_conf,
                "high_confidence_count": high_conf_count,
                "low_confidence_count": low_conf_count,
                "discrepancy": discrepancy,
                "abs_discrepancy": abs_discrepancy,
            }

        # Rule 3 (GREEN): High average confidence (>= 0.60), low occlusion ratio, and matching count (diff <= 1)
        reason_suffix = (
            f" closely matching registered attendance ({registered_count})."
            if registered_count is not None
            else " with high visual reliability."
        )
        return {
            "status_color": "GREEN",
            "status_label": "High Confidence & Reliable Headcount",
            "status_reason": (
                f"Detected {detected_count} persons with high average confidence ({avg_conf:.2f})"
                f"{reason_suffix}"
            ),
            "average_confidence": avg_conf,
            "min_confidence": min_conf,
            "max_confidence": max_conf,
            "high_confidence_count": high_conf_count,
            "low_confidence_count": low_conf_count,
            "discrepancy": discrepancy,
            "abs_discrepancy": abs_discrepancy,
        }

    @staticmethod
    def _annotate_image(
        pil_image: Image.Image,
        detections: list[dict[str, Any]],
        output_image_path: str | Path | None = None,
    ) -> str:
        """
        Purpose:
            Draw color-coded bounding boxes and `#index (conf)` labels on a copy
            of the classroom photo and return a Base64 JPEG Data URL.
        Inputs:
            pil_image (Image.Image): Original RGB classroom image.
            detections (list[dict[str, Any]]): List of person detections with `bbox` and `confidence`.
            output_image_path (str | Path | None): Optional file path to save the annotated JPEG.
        Outputs:
            str: Base64 Data URL (`data:image/jpeg;base64,...`) of the annotated image.
        """
        annotated = pil_image.copy()
        draw = ImageDraw.Draw(annotated)
        line_width = max(2, int(min(annotated.size) / 350))

        for det in detections:
            x1, y1, x2, y2 = det["bbox"]
            conf = float(det["confidence"])
            idx = det["index"]

            # Color-code each individual bounding box by its detection confidence
            if conf >= HIGH_CONF_BOX_THRESHOLD:
                box_color = "#22c55e"  # Green
            elif conf >= LOW_CONF_BOX_THRESHOLD:
                box_color = "#f59e0b"  # Orange
            else:
                box_color = "#ef4444"  # Red

            draw.rectangle([x1, y1, x2, y2], outline=box_color, width=line_width)
            label_text = f"#{idx} {conf:.2f}"
            text_y = max(0, y1 - 16)
            draw.rectangle([x1, text_y, x1 + 64, text_y + 16], fill=box_color)
            draw.text((x1 + 3, text_y + 1), label_text, fill="#ffffff")

        if output_image_path is not None:
            out_path = Path(output_image_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            annotated.save(out_path, format="JPEG", quality=88)

        buffer = io.BytesIO()
        annotated.save(buffer, format="JPEG", quality=85)
        b64_jpeg = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64_jpeg}"

    def _run_yolo_person_detection(
        self, pil_image: Image.Image, conf_threshold: float
    ) -> list[dict[str, Any]]:
        """
        Purpose:
            Run local YOLO inference filtering strictly for `person` (`class_id == 0`)
            and extract sorted bounding boxes and confidence scores.
        Inputs:
            pil_image (Image.Image): Input RGB classroom photo.
            conf_threshold (float): Minimum confidence threshold (`0.0`..`1.0`).
        Outputs:
            list[dict[str, Any]]: Sorted list of detection dictionaries (`index`, `bbox`, `confidence`, `tier`).
        """
        model = self._get_model()
        results = model.predict(
            source=pil_image,
            classes=[PERSON_CLASS_ID],
            conf=conf_threshold,
            imgsz=self.imgsz,
            verbose=False,
        )
        if not results:
            return []

        first_result = results[0]
        raw_boxes = getattr(first_result, "boxes", None)
        if raw_boxes is None:
            return []

        extracted: list[dict[str, Any]] = []
        for box in raw_boxes:
            cls_id = int(box.cls[0]) if hasattr(box.cls, "__getitem__") else int(box.cls)
            if cls_id != PERSON_CLASS_ID:
                continue
            conf_val = (
                float(box.conf[0])
                if hasattr(box.conf, "__getitem__")
                else float(box.conf)
            )
            if conf_val < conf_threshold:
                continue

            coords = box.xyxy[0].tolist() if hasattr(box.xyxy[0], "tolist") else list(box.xyxy[0])
            x1, y1, x2, y2 = [round(float(v), 1) for v in coords[:4]]
            tier = (
                "high"
                if conf_val >= HIGH_CONF_BOX_THRESHOLD
                else ("medium" if conf_val >= LOW_CONF_BOX_THRESHOLD else "low")
            )
            extracted.append(
                {
                    "bbox": [x1, y1, x2, y2],
                    "confidence": round(conf_val, 3),
                    "tier": tier,
                }
            )

        # Sort detections from highest confidence to lowest and assign 1-based index
        extracted.sort(key=lambda item: item["confidence"], reverse=True)
        for i, item in enumerate(extracted, start=1):
            item["index"] = i
        return extracted

    def analyze_classroom_photo(
        self,
        image_source: str | Path | bytes,
        lesson_id: str | None = None,
        registered_count: int | None = None,
        conf_threshold: float | None = None,
        output_image_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """
        Purpose:
            Perform an end-to-end local YOLO classroom student headcount on `image_source`,
            compare with self-registered attendance for `lesson_id`, produce an annotated
            bounding-box image, compute the GREEN / ORANGE / RED status indicator, and
            persist the result in Firebase RTDB.
        Inputs:
            image_source (str | Path | bytes): Classroom photo path or uploaded bytes.
            lesson_id (str | None): Optional lesson session ID to link and compare against.
            registered_count (int | None): Optional explicit self-registered attendance count.
            conf_threshold (float | None): Optional override for `config.yolo_confidence_threshold`.
            output_image_path (str | Path | None): Optional path to save annotated JPEG on disk.
        Outputs:
            dict[str, Any]: Complete headcount report including `detected_count`,
            `registered_count`, `status_color`, `detections`, and `annotated_image_data_url`.
        """
        pil_img = self._load_pil_image(image_source)
        active_threshold = (
            float(conf_threshold)
            if conf_threshold is not None
            else self.config.yolo_confidence_threshold
        )

        # Resolve registered_count from Firebase RTDB session if lesson_id is provided
        resolved_registered = registered_count
        if resolved_registered is None and lesson_id and lesson_id.strip():
            session = self.repository.get(f"/attendance_sessions/{lesson_id.strip()}")
            if isinstance(session, dict) and isinstance(session.get("records"), dict):
                resolved_registered = sum(
                    1
                    for rec in session["records"].values()
                    if isinstance(rec, dict) and rec.get("status") == "present"
                )

        detections = self._run_yolo_person_detection(pil_img, active_threshold)
        confidences = [float(d["confidence"]) for d in detections]
        detected_count = len(detections)

        indicator = self.evaluate_confidence_indicator(
            detected_count=detected_count,
            confidences=confidences,
            registered_count=resolved_registered,
        )
        annotated_data_url = self._annotate_image(
            pil_image=pil_img,
            detections=detections,
            output_image_path=output_image_path,
        )

        analyzed_at = int(time.time())
        report: dict[str, Any] = {
            "lesson_id": lesson_id.strip() if lesson_id else None,
            "detected_count": detected_count,
            "registered_count": resolved_registered,
            "discrepancy": indicator["discrepancy"],
            "abs_discrepancy": indicator["abs_discrepancy"],
            "status_color": indicator["status_color"],
            "status_label": indicator["status_label"],
            "status_reason": indicator["status_reason"],
            "average_confidence": indicator["average_confidence"],
            "min_confidence": indicator["min_confidence"],
            "max_confidence": indicator["max_confidence"],
            "high_confidence_count": indicator["high_confidence_count"],
            "low_confidence_count": indicator["low_confidence_count"],
            "confidence_threshold": active_threshold,
            "image_width": pil_img.width,
            "image_height": pil_img.height,
            "detections": detections,
            "annotated_image_data_url": annotated_data_url,
            "analyzed_at": analyzed_at,
        }

        if lesson_id and lesson_id.strip():
            clean_lesson = lesson_id.strip()
            # Persist compact headcount summary in Firebase RTDB under /attendance_sessions/{lesson_id}/yolo_headcount
            rtdb_summary = {
                k: v for k, v in report.items() if k != "annotated_image_data_url"
            }
            self.repository.set(
                f"/attendance_sessions/{clean_lesson}/yolo_headcount",
                rtdb_summary,
            )

        return report
