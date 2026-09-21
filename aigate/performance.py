"""Repeatable startup and end-to-end performance measurements."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import threading
import time


class PerformanceManager:
    def __init__(self, data_root: str | Path, maximum_samples: int = 1000):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "performance.json"
        self.samples = deque(maxlen=maximum_samples)
        self.lock = threading.RLock()

    @staticmethod
    def _memory() -> dict:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            return {"total_bytes": pages * size}
        except (AttributeError, ValueError, OSError):
            return {"total_bytes": None}

    @staticmethod
    def _gpu() -> dict:
        try:
            import torch
            available = bool(torch.cuda.is_available())
            return {"available": available, "count": torch.cuda.device_count() if available else 0,
                    "name": torch.cuda.get_device_name(0) if available else None}
        except Exception:
            return {"available": False, "count": 0, "name": None}

    @staticmethod
    def _cpu_score(duration: float = 0.12) -> float:
        start = time.perf_counter()
        count = 0
        value = 0x12345678
        while time.perf_counter() - start < duration:
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
            count += 1
        return round(count / max(time.perf_counter() - start, 1e-9), 1)

    def startup_benchmark(self) -> dict:
        scores = [self._cpu_score() for _ in range(3)]
        gpu = self._gpu()
        cores = os.cpu_count() or 1
        memory = self._memory()
        total = memory["total_bytes"] or 0
        if gpu["available"]:
            recommendation = {"profile": "accuracy", "vehicle_model": "ultralytics-yolo26s", "imgsz": 1280, "frame_stride": 1}
        elif cores >= 8 and total >= 12 * 1024**3:
            recommendation = {"profile": "balanced", "vehicle_model": "ultralytics-yolo26n", "imgsz": 960, "frame_stride": 1}
        else:
            recommendation = {"profile": "speed", "vehicle_model": "ultralytics-yolo11n", "imgsz": 640, "frame_stride": 2}
        report = {"measured_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                  "python": platform.python_version(), "cpu_count": cores,
                  "cpu_iterations_per_second": round(statistics.median(scores), 1),
                  "memory": memory, "gpu": gpu, "recommendation": recommendation}
        self.path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def startup_report(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return self.startup_benchmark()

    def current_resources(self) -> dict:
        result = {"load_average": None, "memory_rss_bytes": None, "gpu_memory_bytes": None}
        try:
            result["load_average"] = list(os.getloadavg())
        except (AttributeError, OSError):
            pass
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            result["memory_rss_bytes"] = int(usage * (1024 if platform.system() != "Darwin" else 1))
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                result["gpu_memory_bytes"] = int(torch.cuda.memory_allocated())
        except Exception:
            pass
        return result

    def record(self, *, job_id: str, frame_ms: float, preprocess_ms: float = 0,
               detection_ms: float = 0, plate_ms: float = 0, ocr_ms: float = 0,
               decision_ms: float = 0, storage_ms: float = 0, notification_ms: float = 0) -> None:
        with self.lock:
            self.samples.append({"job_id": job_id, "frame_ms": frame_ms, "preprocess_ms": preprocess_ms,
                                 "detection_ms": detection_ms, "plate_ms": plate_ms, "ocr_ms": ocr_ms,
                                 "decision_ms": decision_ms, "storage_ms": storage_ms,
                                 "notification_ms": notification_ms})

    def summary(self) -> dict:
        with self.lock:
            rows = list(self.samples)
        if not rows:
            return {"samples": 0, "fps": None, "latency_ms": {}, "realtime": None}
        frame = [row["frame_ms"] for row in rows if row["frame_ms"] > 0]
        def percentile(values, ratio):
            ordered = sorted(values)
            return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * ratio))] if ordered else None
        keys = ("frame_ms", "preprocess_ms", "detection_ms", "plate_ms", "ocr_ms", "decision_ms", "storage_ms", "notification_ms")
        latency = {key: {"p50": round(percentile([r[key] for r in rows], .5), 2),
                         "p95": round(percentile([r[key] for r in rows], .95), 2)} for key in keys}
        p95 = latency["frame_ms"]["p95"]
        return {"samples": len(rows), "fps": round(1000 / statistics.mean(frame), 2) if frame else None,
                "latency_ms": latency, "realtime": p95 <= 100}
