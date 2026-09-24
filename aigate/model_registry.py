"""Curated registry of open-source model families relevant to gate recognition."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import importlib.util
import os
from pathlib import Path


class ModelRole(str, Enum):
    VEHICLE = "vehicle_detection"
    PLATE = "plate_detection"
    OCR = "ocr"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    name: str
    role: ModelRole
    provider: str
    family: str
    license: str
    source: str
    backend: str
    artifact: str | None = None
    japanese: bool = False
    realtime: bool = False
    bundled: bool = False
    notes: str = ""

    def json(self, available: bool, reason: str) -> dict:
        data = asdict(self)
        data["role"] = self.role.value
        data.update(available=available, availability_reason=reason)
        data["license_scope"] = "コードの条件。重み・学習データの条件は別途確認"
        data["license_review"] = "conditional" if self.id.startswith("ultralytics-") or self.id in {"custom-yolo-plate", "fast-plate-ocr-jp"} else "code_license_identified"
        return data


SPECS = (
    ModelSpec("ultralytics-yolo26n", "YOLO26n", ModelRole.VEHICLE, "Ultralytics", "YOLO26", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo26n.pt", realtime=True, bundled=True, notes="最新の軽量リアルタイム候補"),
    ModelSpec("ultralytics-yolo26s", "YOLO26s", ModelRole.VEHICLE, "Ultralytics", "YOLO26", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo26s.pt", realtime=True, bundled=True),
    ModelSpec("ultralytics-yolo11n", "YOLO11n", ModelRole.VEHICLE, "Ultralytics", "YOLO11", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo11n.pt", realtime=True, bundled=True),
    ModelSpec("ultralytics-yolov8n", "YOLOv8n", ModelRole.VEHICLE, "Ultralytics", "YOLOv8", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolov8n.pt", realtime=True, bundled=True),
    ModelSpec("opencv-plate-contours", "OpenCV輪郭抽出", ModelRole.PLATE, "OpenCV", "Classical CV", "Apache-2.0", "https://github.com/opencv/opencv", "cv2", realtime=True, bundled=True, notes="学習済み重み不要のフォールバック"),
    ModelSpec("custom-yolo-plate", "専用YOLOプレートモデル", ModelRole.PLATE, "Local", "YOLO custom", "モデル提供元に従う", "local://models/plate", "ultralytics", artifact="GATE_PLATE_MODEL", realtime=True, bundled=True, notes="GATE_PLATE_MODELで指定"),
    ModelSpec("paddle-ppocr-v6", "PP-OCRv6", ModelRole.OCR, "PaddlePaddle", "PP-OCRv6", "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR", "paddleocr", "PP-OCRv6_medium_rec", japanese=True, realtime=True, bundled=True),
    ModelSpec("lipla-jp", "Lipla-jp EdgeCrafter + PPOCRv6", ModelRole.OCR, "ikeboo", "Lipla-jp", "MIT", "https://github.com/ikeboo/Lipla-jp", "lipla", japanese=True, realtime=True, bundled=True, notes="日本ナンバープレート検出・認識一体型"),
    ModelSpec("fast-alpr", "FastALPR + fast-plate-ocr", ModelRole.OCR, "ankandrew", "FastALPR/CCT", "MIT", "https://github.com/ankandrew/fast-alpr", "fast_alpr", realtime=True, notes="ONNX検出＋OCR。日本向け未学習"),
    ModelSpec("fast-plate-ocr-jp", "FastPlateOCR 日本向け追加学習モデル", ModelRole.OCR, "AI GATE SYSTEM", "CCT fine-tune", "利用条件未確定 / 基盤モデル条件に従う", "https://github.com/UeEmon/AI-Gate-JP-Models", "fast_plate_ocr", artifact="GATE_FAST_OCR_MODEL_PATH", japanese=True, realtime=True, notes="日本プレート画像で追加学習後のONNX＋plate_config。学習済み重みは未公開"),
    ModelSpec("paddle-ppocr-v5", "PP-OCRv5 multilingual", ModelRole.OCR, "PaddlePaddle", "PP-OCRv5", "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR", "paddleocr", "PP-OCRv5_server_rec", japanese=True, realtime=True, bundled=True),
    ModelSpec("easyocr-ja", "EasyOCR Japanese", ModelRole.OCR, "JaidedAI", "EasyOCR", "Apache-2.0", "https://github.com/JaidedAI/EasyOCR", "easyocr", japanese=True, realtime=True, bundled=True),
)


class ModelRegistry:
    def __init__(self, model_root: str | Path = "/models", data_root=None):
        self.model_root = Path(model_root)
        self.data_root = data_root
        self._items = {spec.id: spec for spec in SPECS}

    def fast_paths(self):
        if self.data_root is not None:
            from ocr_learning import active_fast_model
            active = active_fast_model(self.data_root)
            if active:
                return active['model'], active['config']
        return os.getenv('GATE_FAST_OCR_MODEL_PATH', ''), os.getenv('GATE_FAST_OCR_CONFIG_PATH', '')

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._items[model_id]
        except KeyError as exc:
            raise ValueError(f"未知のモデルです: {model_id}") from exc

    def availability(self, spec: ModelSpec) -> tuple[bool, str]:
        if spec.id == "custom-yolo-plate":
            configured = os.getenv("GATE_PLATE_MODEL", "")
            return (bool(configured and Path(configured).is_file()), "専用重み設定済み" if configured and Path(configured).is_file() else "GATE_PLATE_MODELに学習済み重みを指定してください")
        if spec.id == "fast-plate-ocr-jp":
            model, config = self.fast_paths()
            available = bool(model and config and Path(model).is_file() and Path(config).is_file())
            return available, "日本向け重み・設定済み" if available else "追加学習済みONNXとplate_config.yamlを指定してください"
        module = spec.backend.split(".")[0]
        available = importlib.util.find_spec(module) is not None
        return available, "利用可能" if available else f"追加パッケージ {module} が必要です"

    def list(self, role: ModelRole | None = None) -> list[dict]:
        result = []
        for spec in self._items.values():
            if role is not None and spec.role != role:
                continue
            available, reason = self.availability(spec)
            result.append(spec.json(available, reason))
        return result

    def validate_selection(self, settings: dict) -> None:
        mapping = {"vehicle_model": ModelRole.VEHICLE, "plate_model": ModelRole.PLATE, "ocr_model": ModelRole.OCR}
        for key, role in mapping.items():
            spec = self.get(settings[key])
            if spec.role != role:
                raise ValueError(f"{key}に指定されたモデル種別が不正です。")
            available, reason = self.availability(spec)
            if not available:
                raise ValueError(f"{spec.name}は選択できません: {reason}")

    def vehicle_argument(self, model_id: str) -> str:
        spec = self.get(model_id)
        if spec.backend != "ultralytics" or not spec.artifact:
            raise ValueError("現在のリアルタイム実行アダプターではこの車両モデルを利用できません。")
        path = self.model_root / spec.artifact
        self.model_root.mkdir(parents=True, exist_ok=True)
        return str(path)

    def ocr_environment(self, model_id: str) -> dict[str, str]:
        if model_id == "easyocr-ja":
            return {"GATE_OCR_BACKEND": "easyocr"}
        if model_id in {"paddle-ppocr-v5", "paddle-ppocr-v6"}:
            model = "PP-OCRv5_server_rec" if model_id.endswith("v5") else "PP-OCRv6_medium_rec"
            return {"GATE_OCR_BACKEND": "paddle", "GATE_PADDLE_MODEL": model}
        if model_id == "lipla-jp":
            return {"GATE_OCR_BACKEND": "lipla"}
        if model_id == "fast-alpr":
            return {"GATE_OCR_BACKEND": "fastalpr"}
        if model_id == "fast-plate-ocr-jp":
            model, config = self.fast_paths()
            if not model or not config or not Path(model).is_file() or not Path(config).is_file():
                raise ValueError("日本向けFastPlateOCRモデルと設定ファイルが必要です。")
            return {"GATE_OCR_BACKEND": "fastalpr", "GATE_FAST_OCR_MODEL_PATH": model,
                    "GATE_FAST_OCR_CONFIG_PATH": config}
        raise ValueError("現在のリアルタイム実行アダプターではこのOCRモデルを利用できません。")

    def plate_environment(self, model_id: str) -> dict[str, str]:
        if model_id == "opencv-plate-contours":
            return {"GATE_PLATE_MODEL": ""}
        if model_id == "custom-yolo-plate":
            configured = os.getenv("GATE_PLATE_MODEL", "")
            if not configured or not Path(configured).is_file():
                raise ValueError("専用プレートモデルが設定されていません。")
            return {"GATE_PLATE_MODEL": configured}
        raise ValueError("現在のリアルタイム実行アダプターではこのプレートモデルを利用できません。")
