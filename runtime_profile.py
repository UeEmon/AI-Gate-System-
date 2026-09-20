"""Bounded startup measurement and conservative CPU inference recommendations."""
import os
import time
from pathlib import Path


def measure():
    cpus = float(len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else os.cpu_count() or 1)
    memory = 2 * 1024**3
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        pass
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max':
            cpus = min(cpus, int(quota) / int(period))
    except (OSError, ValueError):
        pass
    for name in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            memory = min(memory, int(Path(name).read_text()))
        except (OSError, ValueError):
            pass
    # Same fixed workload on every start; no model download or network access.
    start = time.perf_counter()
    loops = 0
    while time.perf_counter() - start < .25:
        sum(i * i for i in range(10000))
        loops += 1
    score = round(loops / (time.perf_counter() - start))
    tier = 'standard' if cpus >= 4 and memory >= 6 * 1024**3 and score >= 1000 else 'light'
    return dict(cpu_count=round(cpus, 2), memory_gib=round(memory / 1024**3, 1),
                score=score, tier=tier, model='yolo26s.pt' if tier == 'standard' else 'yolo26n.pt',
                imgsz=960 if tier == 'standard' else 640, every=1 if tier == 'standard' else 3,
                cameras=2 if tier == 'standard' else 1, ocr='easyocr')


def model_choices(configured):
    directory = Path(configured).parent if Path(configured).is_absolute() else Path('/models')
    choices = {name: str(directory / name) if directory.is_absolute() else name
               for name in ('yolo11n.pt', 'yolo26n.pt', 'yolo26s.pt')}
    choices[Path(configured).name] = str(configured)
    if directory.is_dir():
        for path in sorted(directory.glob('*.pt')):
            if path.is_file() and not path.is_symlink():
                choices[path.name] = str(path)
    return choices
