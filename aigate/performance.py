"""Repeatable startup and end-to-end performance measurements."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import math
import subprocess
import threading
import time


class PerformanceManager:
    def __init__(self, data_root: str | Path, maximum_samples: int = 1000):
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "performance.json"
        self.samples = deque(maxlen=maximum_samples)
        self.lock = threading.RLock()
        self.offsets = {}
        self.total_samples = 0
        self.job_totals = {}
        self.process_cpu = {}

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
            recommendation = {"profile": "accuracy", "imgsz": 1280, "frame_stride": 1}
        elif cores >= 8 and total >= 12 * 1024**3:
            recommendation = {"profile": "balanced", "imgsz": 960, "frame_stride": 1}
        else:
            recommendation = {"profile": "speed", "imgsz": 640, "frame_stride": 2}
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
        from .telemetry import resources
        result = resources()
        result['load_average'] = list(os.getloadavg()) if hasattr(os, 'getloadavg') else None
        # Include the resident model and all active gate workers, rather than web RSS alone.
        try:
            import psutil
            parent = psutil.Process()
            family = [parent] + parent.children(recursive=True)
            alive = []
            for process in family:
                try:
                    key = (process.pid, process.create_time())
                    cpu = self.process_cpu.setdefault(key, process)
                    alive.append((process.memory_info().rss, cpu.cpu_percent(None)))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            result['memory_rss_bytes'] = sum(x[0] for x in alive)
            result['gate_cpu_percent'] = sum(x[1] for x in alive)
            self.process_cpu = {k:v for k,v in self.process_cpu.items() if v.is_running()}
        except Exception:
            # Restricted process namespaces may hide our PID; report unavailable.
            pass
        try:
            value = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used',
                                    '--format=csv,noheader,nounits'], capture_output=True,
                                   text=True, timeout=.5, check=True).stdout.splitlines()
            cards = [tuple(float(x.strip()) for x in line.split(',')) for line in value]
            result['gpu_utilization_percent'] = max(c[0] for c in cards)
            result['gpu_memory_bytes'] = int(sum(c[1] for c in cards)*1024**2)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return result

    def ingest(self):
        """Read every complete telemetry row exactly once, including finished jobs."""
        with self.lock:
            for path in sorted((self.root / 'jobs').glob('*/performance.jsonl')):
                key = str(path)
                offset = self.offsets.get(key, 0)
                if path.stat().st_size < offset:
                    offset = 0
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line or not line.endswith(b'\n'):
                            stream.seek(start)
                            break
                        try:
                            row = json.loads(line)
                            self.record(job_id=path.parent.name, **{k:v for k,v in row.items()
                                if k not in ('frame_index', 'elapsed_ms', 'measured_at', 'scope', 'excludes', 'timing_scope')})
                            self.job_totals[path.parent.name] = dict(frames=self.job_totals.get(path.parent.name, {}).get('frames',0)+1,
                                                                   elapsed_ms=row['elapsed_ms'])
                        except (ValueError, TypeError, KeyError):
                            pass
                    self.offsets[key] = stream.tell()

    def record(self, *, job_id, frame_ms, resources=None, **stages):
        if not math.isfinite(frame_ms) or frame_ms < 0:
            raise ValueError('Invalid frame duration')
        keys = ('processing_ms', 'capture_ms', 'preprocess_ms', 'detection_ms', 'plate_ms', 'rectification_ms', 'ocr_ms',
                'plate_recognition_ms', 'decision_ms', 'storage_ms', 'notification_ms')
        with self.lock:
            self.samples.append(dict(job_id=job_id, frame_ms=frame_ms,
                resources=resources or {}, **{k: stages.get(k, 0) for k in keys}))
            self.total_samples += 1

    def summary(self) -> dict:
        self.ingest()
        with self.lock:
            rows = list(self.samples)
            total = self.total_samples
            jobs = dict(self.job_totals)
        if not rows:
            return dict(samples=0, total_samples=total, fps=None, latency_ms={}, realtime=None)
        def stats(values):
            values = sorted(v for v in values if v is not None and math.isfinite(v))
            if not values:
                return None
            return dict(mean=round(statistics.mean(values),2),
                        p50=round(values[math.ceil(len(values)*.5)-1],2),
                        p95=round(values[math.ceil(len(values)*.95)-1],2))
        keys = ('frame_ms', 'processing_ms', 'capture_ms', 'preprocess_ms', 'detection_ms', 'plate_ms', 'rectification_ms', 'ocr_ms',
                'plate_recognition_ms', 'decision_ms', 'storage_ms', 'notification_ms')
        latency = {k:stats([r[k] for r in rows]) for k in keys}
        resource_keys = ('cpu_percent','memory_rss_bytes','memory_percent',
                         'gpu_utilization_percent','gpu_memory_bytes')
        return dict(samples=len(rows), total_samples=total, jobs=jobs,
                    fps=round(1000/statistics.mean(r['frame_ms'] for r in rows),2)
                        if any(r['frame_ms'] for r in rows) else None,
                    processing_fps_scope='processed_frames_includes_input_wait_and_decode; not camera source FPS',
                    latency_ms=latency, resources={k:stats([r['resources'].get(k) for r in rows]) for k in resource_keys},
                    processing_only_fps=round(1000/statistics.mean(r['processing_ms'] for r in rows),2) if any(r['processing_ms'] for r in rows) else None,
                    timing_scope='Lipla detection/rectification/OCR combined; plate_ms and rectification_ms not independently measurable',
                    realtime=latency['frame_ms']['p95'] <= 100)
