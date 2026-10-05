"""Model-only experiment adapters. Never enabled on main or by default."""
from contextlib import contextmanager
from importlib.metadata import version
import os

_DEFAULT_TORCH_THREADS = None


class PoseProbe:
    """Capture native detection output without an additional inference pass."""
    def __init__(self, model):
        self.model, self.last_result = model, None

    def __call__(self, image):
        self.last_result = self.model(image)
        return self.last_result

    def __getattr__(self, name):
        return getattr(self.model, name)


@contextmanager
def lipla_threads(count):
    if not count:
        yield
        return
    import onnxruntime as ort
    from lipla.inferencers import pose_detector, ppocr
    modules = [pose_detector, ppocr]
    originals = [m.create_inference_session for m in modules]
    def wrapper(original):
        def create(*args, **kwargs):
            options = kwargs.get('session_options') or ort.SessionOptions()
            options.intra_op_num_threads = count
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.add_session_config_entry('session.intra_op.allow_spinning', '0')
            kwargs['session_options'] = options
            return original(*args, **kwargs)
        return create
    try:
        for module, original in zip(modules, originals):
            module.create_inference_session = wrapper(original)
        yield
    finally:
        for module, original in zip(modules, originals):
            module.create_inference_session = original


def conditional_class():
    # This opt-in adapter mirrors a verified upstream method, with the default
    # full recognition retained whenever plain OCR is uncertain or plate is local.
    if version('lipla-jp') != '0.4.1':
        raise RuntimeError('条件付きOCRはLipla-jp 0.4.1のみ検証対象です。')
    import numpy as np
    from lipla.core.license_plate_recognizer import Recognizer, as_bgr, pad_for_ocr
    from app import parse_plate
    class ConditionalRecognizer(Recognizer):
        fast_count = fallback_count = 0
        def _recognize_detection(self, image, raw_vertices, detection_score, class_name):
            if str(class_name).casefold() == 'local':
                self.fallback_count += 1
                return super()._recognize_detection(image, raw_vertices, detection_score, class_name)
            vertices = np.asarray(raw_vertices, dtype=np.float32).reshape(-1, 2)
            if vertices.shape != (4,2) or not np.all(np.isfinite(vertices)):
                return None
            normalized = as_bgr(self.plate_normalizer.normalize(
                image, np.ascontiguousarray(vertices[[0,3,2,1]], dtype=np.float32)))
            padded, padding = pad_for_ocr(normalized)
            ocr = self.ocr_model(padded)
            kwargs = dict(vertices=vertices, detection_score=float(detection_score),
                          normalized_image=normalized, original_image=image,
                          class_name=str(class_name), padding=padding)
            result = self.parse_ocr_result(ocr, **kwargs)
            text = f'{result.area} {result.class_number} {result.kana} {result.number}'
            if min(result.area_score,result.class_number_score,result.kana_score,result.number_score)>=.98 and parse_plate(text):
                self.fast_count += 1
                return result
            self.fallback_count += 1
            result = self.parse_ocr_result(ocr, field_candidates=self.field_recognizer.recognize(normalized), **kwargs)
            return result if result.class_number and result.number > 0 else None
    return ConditionalRecognizer


def prepare_vehicle(model, root, config):
    global _DEFAULT_TORCH_THREADS
    if config.get('threads') or _DEFAULT_TORCH_THREADS is not None:
        import torch
        if _DEFAULT_TORCH_THREADS is None:
            _DEFAULT_TORCH_THREADS = torch.get_num_threads()
        torch.set_num_threads(config.get('threads') or _DEFAULT_TORCH_THREADS)
    if not config.get('openvino'):
        return model
    from pathlib import Path
    import hashlib
    from ultralytics import YOLO
    source = Path(model.ckpt_path).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    directory = Path(root)/'speed-openvino'/digest
    directory.mkdir(parents=True, exist_ok=True)
    export = directory/'vehicle_openvino_model'
    if not (export/'vehicle.xml').is_file():
        import shutil
        copied = directory/'vehicle.pt'
        shutil.copyfile(source, copied)
        YOLO(str(copied)).export(format='openvino', imgsz=960, dynamic=True, half=False, int8=False)
    return YOLO(str(export), task='detect')
