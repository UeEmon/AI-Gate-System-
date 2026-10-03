"""Append-only per-frame telemetry, independent of browser polling."""
import json
import os
from pathlib import Path
import time


def resources(include_gpu=False):
    result = dict(cpu_percent=None, memory_rss_bytes=None, memory_percent=None,
                  gpu_utilization_percent=None, gpu_memory_bytes=None)
    try:
        import psutil
        process = psutil.Process()
        processes = [process] + process.children(recursive=True)
        result.update(memory_rss_bytes=sum(p.memory_info().rss for p in processes if p.is_running()),
                      memory_percent=psutil.virtual_memory().percent)
        result['cpu_percent'] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    if include_gpu:
        import subprocess
        try:
            output=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.used',
                                   '--format=csv,noheader,nounits'],capture_output=True,text=True,
                                  timeout=.5,check=True).stdout.splitlines()
            cards=[tuple(float(x.strip()) for x in line.split(',')) for line in output]
            result['gpu_utilization_percent']=max(x[0] for x in cards)
            result['gpu_memory_bytes']=int(sum(x[1] for x in cards)*1024**2)
        except (OSError,ValueError,subprocess.SubprocessError):
            pass
    return result


class FrameTelemetry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a', encoding='utf-8')
        self.started = time.perf_counter()
        self.last_resources = 0
        self.snapshot = {}

    def record(self, frame_index, measured):
        now = time.perf_counter()
        if now - self.last_resources >= 1:
            self.snapshot = resources()
            self.last_resources = now
        combined_resources = dict(self.snapshot)
        model_resources = measured.pop('model_resources',{})
        if combined_resources.get('memory_rss_bytes') is not None and model_resources.get('memory_rss_bytes') is not None:
            combined_resources['memory_rss_bytes'] += model_resources['memory_rss_bytes']
        for key in ('gpu_utilization_percent','gpu_memory_bytes'):
            if model_resources.get(key) is not None:
                combined_resources[key] = model_resources[key]
        row = dict(measured, frame_index=frame_index, elapsed_ms=(now-self.started)*1000,
                   resources=combined_resources, measured_at=time.time(),
                   scope='capture_decode_preprocess_inference_save_and_notification_queue',
                   excludes=['async_email_delivery', 'async_vehicle_identity'])
        self.stream.write(json.dumps(row, allow_nan=False) + '\n')
        self.stream.flush()

    def close(self):
        self.stream.close()


class TimedInput:
    def __init__(self, stream):
        self.stream = iter(stream)
        self.capture_ms = 0

    def __iter__(self):
        return self

    def __next__(self):
        started = time.perf_counter()
        item = next(self.stream)
        self.capture_ms = (time.perf_counter()-started)*1000
        return item
