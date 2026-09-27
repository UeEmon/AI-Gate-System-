"""Installed inference models. The Web API does not expose model selection."""
from dataclasses import asdict, dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import os


class ModelRole(str, Enum):
    VEHICLE = 'vehicle_detection'
    PLATE = 'plate_detection'
    OCR = 'ocr'


@dataclass(frozen=True, slots=True)
class ModelSpec:
    id: str
    name: str
    role: ModelRole
    provider: str
    license: str
    source: str
    backend: str
    artifact: str | None = None

    def json(self, available, reason):
        return {**asdict(self), 'role': self.role.value, 'available': available,
                'availability_reason': reason, 'license_scope': 'コードの条件。重みは別途確認'}


SPECS = (
    ModelSpec('ultralytics-yolo26n', 'YOLO26n 車両検出', ModelRole.VEHICLE,
              'Ultralytics', 'AGPL-3.0 / Enterprise',
              'https://github.com/ultralytics/ultralytics', 'ultralytics', 'yolo26n.pt'),
    ModelSpec('lipla-native-plate', 'Lipla-jp プレート検出', ModelRole.PLATE,
              'ikeboo', 'MIT', 'https://github.com/ikeboo/Lipla-jp', 'lipla'),
    ModelSpec('lipla-jp', 'Lipla-jp OCR', ModelRole.OCR,
              'ikeboo', 'MIT', 'https://github.com/ikeboo/Lipla-jp', 'lipla'),
)


class ModelRegistry:
    def __init__(self, model_root: str | Path = '/models', data_root=None):
        self.model_root = Path(model_root)
        self._items = {spec.id: spec for spec in SPECS}

    def list(self, role=None):
        items = []
        for spec in SPECS:
            if role is not None and role != spec.role:
                continue
            available = importlib.util.find_spec(spec.backend) is not None
            items.append(spec.json(available, '利用可能' if available else f'{spec.backend} が必要です'))
        return items

    def get(self, model_id):
        try:
            return self._items[model_id]
        except KeyError as error:
            raise ValueError(f'未対応のモデルです: {model_id}') from error

    def vehicle_argument(self, model_id):
        spec = self.get(model_id)
        if spec.role != ModelRole.VEHICLE:
            raise ValueError('車両モデルは固定されています。')
        self.model_root.mkdir(parents=True, exist_ok=True)
        return str(self.model_root / spec.artifact)

    def ocr_environment(self, model_id):
        if model_id != 'lipla-jp':
            raise ValueError('OCRはLipla-jpに固定されています。')
        return {'GATE_OCR_BACKEND': 'lipla'}

    def plate_environment(self, model_id):
        if model_id != 'lipla-native-plate':
            raise ValueError('プレート検出はLipla-jpに固定されています。')
        mode = os.getenv('GATE_PLATE_PIPELINE', 'lipla').lower()
        if mode not in ('lipla', 'paddle'):
            raise ValueError('プレート認識方式はliplaまたはpaddleを指定してください。')
        if mode == 'paddle':
            path = os.getenv('GATE_PADDLE_PLATE_WEIGHTS')
            recognition = os.getenv('GATE_PADDLE_REC_MODEL_DIR')
            if not path or not Path(path).is_file() or not recognition or not (Path(recognition) / 'inference.pdiparams').is_file():
                raise ValueError('Paddle方式には学習済みの専用検出重みとOCR推論モデルが必要です。')
            return {'GATE_PLATE_PIPELINE': 'paddle', 'GATE_PLATE_MODEL': path,
                    'GATE_PADDLE_REC_MODEL_DIR': recognition}
        return {'GATE_PLATE_PIPELINE': 'lipla', 'GATE_PLATE_MODEL': ''}
