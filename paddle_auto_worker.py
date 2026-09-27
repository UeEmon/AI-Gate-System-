"""Run PaddleOCR training in a separate container, polling reviewed samples."""
import argparse
from pathlib import Path
import signal
import threading

from paddle_auto_train import PaddleTrainingManager


def run(root, interval=30):
    manager = PaddleTrainingManager(root)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            manager.maybe_start()
            manager.maybe_compare()
        except Exception as error:
            with manager.lock:
                manager._save(dict(state='failed', error=str(error)))
        stop.wait(interval)
    if manager.process is not None and manager.process.poll() is None:
        manager.process.terminate()
        try:
            manager.process.wait(timeout=20)
        except Exception:
            manager.process.kill()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    args = parser.parse_args()
    run(args.data)
