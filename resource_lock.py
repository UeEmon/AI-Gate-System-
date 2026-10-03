"""Serialize heavyweight training/evaluation and tuning across Docker containers."""
from pathlib import Path
import fcntl


class ResourceLease:
    def __init__(self,root):
        self.path=Path(root)/'heavy-compute.lock'
        self.stream=None

    def acquire(self):
        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open('a')
        try:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close();return False
        self.stream=stream;return True

    def release(self):
        if self.stream is not None:
            fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN)
            self.stream.close();self.stream=None
