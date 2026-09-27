"""Atomic, validated runtime settings storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading


DEFAULTS = {
    "vehicle_model": "ultralytics-yolo26n",
    "plate_model": "lipla-native-plate",
    "ocr_model": "lipla-jp",
    "profile": "auto",
    "imgsz": 960,
    "frame_stride": 1,
}


class SettingsManager:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "system-settings.json"
        self.lock = threading.RLock()

    def read(self) -> dict:
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                raw = {}
            if raw and not raw.get("_lipla_default_migrated"):
                if (raw.get("plate_model", "opencv-plate-contours") == "opencv-plate-contours"
                        and raw.get("ocr_model", "paddle-ppocr-v6") == "paddle-ppocr-v6"):
                    raw["plate_model"] = "lipla-native-plate"
                    raw["ocr_model"] = "lipla-jp"
                raw["_lipla_default_migrated"] = True
                self._save(raw)
            return {**DEFAULTS, **{key: raw[key] for key in DEFAULTS if key in raw}}

    def _save(self, content: dict) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(content, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def update(self, changes: dict) -> dict:
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise ValueError("未対応の設定です: " + ", ".join(sorted(unknown)))
        current = self.read()
        current.update(changes)
        current["imgsz"] = min(1920, max(320, int(current["imgsz"])))
        current["frame_stride"] = min(1000, max(1, int(current["frame_stride"])))
        if current["profile"] not in {"auto", "speed", "balanced", "accuracy"}:
            raise ValueError("性能プロファイルが不正です。")
        with self.lock:
            self._save({**current, "_lipla_default_migrated": True})
        return current
