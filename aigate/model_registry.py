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
        return data


SPECS = (
    ModelSpec("ultralytics-yolo26n", "YOLO26n", ModelRole.VEHICLE, "Ultralytics", "YOLO26", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo26n.pt", realtime=True, bundled=True, notes="最新の軽量リアルタイム候補"),
    ModelSpec("ultralytics-yolo26s", "YOLO26s", ModelRole.VEHICLE, "Ultralytics", "YOLO26", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo26s.pt", realtime=True, bundled=True),
    ModelSpec("ultralytics-yolo11n", "YOLO11n", ModelRole.VEHICLE, "Ultralytics", "YOLO11", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolo11n.pt", realtime=True, bundled=True),
    ModelSpec("ultralytics-yolov8n", "YOLOv8n", ModelRole.VEHICLE, "Ultralytics", "YOLOv8", "AGPL-3.0 / Enterprise", "https://github.com/ultralytics/ultralytics", "ultralytics", "yolov8n.pt", realtime=True, bundled=True),
    ModelSpec("rtdetr-r18", "RT-DETR R18", ModelRole.VEHICLE, "Baidu", "RT-DETR", "Apache-2.0", "https://github.com/lyuwenyu/RT-DETR", "rtdetr", realtime=True, notes="追加依存が必要"),
    ModelSpec("mmdetection", "MMDetection model zoo", ModelRole.VEHICLE, "OpenMMLab", "MMDetection", "Apache-2.0", "https://github.com/open-mmlab/mmdetection", "mmdet", notes="モデルごとの重みと設定が必要"),
    ModelSpec("yolox", "YOLOX", ModelRole.VEHICLE, "Megvii", "YOLOX", "Apache-2.0", "https://github.com/Megvii-BaseDetection/YOLOX", "yolox", realtime=True),
    ModelSpec("detectron2", "Detectron2 model zoo", ModelRole.VEHICLE, "Meta", "Detectron2", "Apache-2.0", "https://github.com/facebookresearch/detectron2", "detectron2"),
    ModelSpec("opencv-plate-contours", "OpenCV輪郭抽出", ModelRole.PLATE, "OpenCV", "Classical CV", "Apache-2.0", "https://github.com/opencv/opencv", "cv2", realtime=True, bundled=True, notes="学習済み重み不要のフォールバック"),
    ModelSpec("custom-yolo-plate", "専用YOLOプレートモデル", ModelRole.PLATE, "Local", "YOLO custom", "モデル提供元に従う", "local://models/plate", "ultralytics", artifact="GATE_PLATE_MODEL", realtime=True, bundled=True, notes="GATE_PLATE_MODELで指定"),
    ModelSpec("lprs-jp", "lprs-jp YOLOv8 + CNN", ModelRole.PLATE, "eepj", "lprs-jp", "未確認（READMEは研究目的と記載）", "https://github.com/eepj/lprs-jp", "lprs-jp", japanese=True, notes="研究用リポジトリ。公開推論API・配布重み・LICENSEの確認後に実行アダプターを追加"),
    ModelSpec("alpr-jp-openalpr", "alpr_jp OpenALPR学習素材", ModelRole.PLATE, "dyama", "OpenALPR + OpenCV + Tesseract", "MIT", "https://github.com/dyama/alpr_jp", "openalpr", japanese=True, notes="日本プレート画像・学習素材。完成済みPython推論モデルではない"),
    ModelSpec("paddle-text-detector", "PaddleOCR text detector", ModelRole.PLATE, "PaddlePaddle", "PP-OCR", "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR", "paddleocr", japanese=True, realtime=True, bundled=True),
    ModelSpec("paddle-ppocr-v6", "PP-OCRv6", ModelRole.OCR, "PaddlePaddle", "PP-OCRv6", "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR", "paddleocr", "PP-OCRv6_medium_rec", japanese=True, realtime=True, bundled=True),
    ModelSpec("lipla-jp", "Lipla-jp EdgeCrafter + PPOCRv6", ModelRole.OCR, "ikeboo", "Lipla-jp", "MIT", "https://github.com/ikeboo/Lipla-jp", "lipla", japanese=True, realtime=True, bundled=True, notes="日本ナンバープレート検出・認識一体型"),
    ModelSpec("fast-alpr", "FastALPR + fast-plate-ocr", ModelRole.OCR, "ankandrew", "FastALPR/CCT", "MIT", "https://github.com/ankandrew/fast-alpr", "fast_alpr", realtime=True, notes="ONNX検出＋OCR。日本向け未学習"),
    ModelSpec("fast-plate-ocr-jp", "FastPlateOCR 日本向け追加学習モデル", ModelRole.OCR, "AI GATE SYSTEM", "CCT fine-tune", "MIT / 基盤モデル条件に従う", "https://github.com/ankandrew/fast-plate-ocr", "fast_plate_ocr", artifact="GATE_FAST_OCR_MODEL_PATH", japanese=True, realtime=True, notes="日本プレート画像で追加学習後のONNX＋plate_config"),
    ModelSpec("paddle-ppocr-v5", "PP-OCRv5 multilingual", ModelRole.OCR, "PaddlePaddle", "PP-OCRv5", "Apache-2.0", "https://github.com/PaddlePaddle/PaddleOCR", "paddleocr", "PP-OCRv5_server_rec", japanese=True, realtime=True, bundled=True),
    ModelSpec("easyocr-ja", "EasyOCR Japanese", ModelRole.OCR, "JaidedAI", "EasyOCR", "Apache-2.0", "https://github.com/JaidedAI/EasyOCR", "easyocr", japanese=True, realtime=True, bundled=True),
    ModelSpec("tesseract-ja", "Tesseract Japanese", ModelRole.OCR, "Tesseract", "Tesseract LSTM", "Apache-2.0", "https://github.com/tesseract-ocr/tesseract", "pytesseract", japanese=True, realtime=True),
    ModelSpec("rapidocr", "RapidOCR", ModelRole.OCR, "RapidAI", "RapidOCR", "Apache-2.0", "https://github.com/RapidAI/RapidOCR", "rapidocr_onnxruntime", japanese=True, realtime=True),
    ModelSpec("mmocr", "MMOCR model zoo", ModelRole.OCR, "OpenMMLab", "MMOCR", "Apache-2.0", "https://github.com/open-mmlab/mmocr", "mmocr", japanese=True),
    ModelSpec("doctr", "docTR", ModelRole.OCR, "Mindee", "docTR", "Apache-2.0", "https://github.com/mindee/doctr", "doctr", notes="文書OCR向け。ナンバー用途は要評価"),
    ModelSpec("trocr", "TrOCR", ModelRole.OCR, "Microsoft", "Transformer OCR", "MIT", "https://github.com/microsoft/unilm/tree/master/trocr", "transformers", japanese=False, notes="標準重みは日本ナンバー向けではない"),
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
        if spec.id == "paddle-text-detector":
            return False, "ナンバープレート専用アダプターの固定評価が未完了です"
        if spec.id == "lprs-jp":
            return False, "研究用コードで、公開推論API・配布重み・LICENSEの確認が未完了です"
        if spec.id == "alpr-jp-openalpr":
            return False, "学習素材リポジトリで、OpenALPR実行環境と完成済み重みが未導入です"
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
