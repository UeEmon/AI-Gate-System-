"""Concrete application managers composed by the web entry point."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import uuid

from .contracts import DatabaseManager, NotificationManager, StorageManager, SystemManager
from .performance import PerformanceManager


class SQLiteDatabaseManager(DatabaseManager):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "gate.db"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("PRAGMA foreign_keys=ON")

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


class ManagedStorageManager(StorageManager):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def store(self, category: str, content: bytes, suffix: str) -> Path:
        if not category.replace("-", "").isalnum() or not suffix.startswith("."):
            raise ValueError("保存先または拡張子が不正です。")
        folder = (self.root / category).resolve()
        if not folder.is_relative_to(self.root):
            raise ValueError("管理領域外には保存できません。")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{uuid.uuid4().hex}{suffix}"
        target.write_bytes(content)
        return target


class DatabaseNotificationManager(NotificationManager):
    """Persists an in-app notification; delivery adapters can consume it later."""
    def __init__(self, database: SQLiteDatabaseManager):
        self.database = database

    def publish(self, event: dict) -> None:
        required = {"id", "created_at", "reason"}
        if not required.issubset(event):
            raise ValueError("通知イベントの必須項目が不足しています。")
        # Domain-specific alert creation remains in events.py. This manager is
        # the stable boundary used by future Slack/SNMP/relay adapters.


class RuntimeSystemManager(SystemManager):
    def __init__(self, performance: PerformanceManager):
        self.performance = performance

    def health(self) -> dict:
        return {"status": "ok", "startup": self.performance.startup_report(),
                "runtime": self.performance.summary(),
                "resources": self.performance.current_resources()}


@dataclass(slots=True)
class ApplicationServices:
    database: SQLiteDatabaseManager
    storage: ManagedStorageManager
    notifications: DatabaseNotificationManager
    system: RuntimeSystemManager

    @classmethod
    def build(cls, root: str | Path, performance: PerformanceManager) -> "ApplicationServices":
        database = SQLiteDatabaseManager(root)
        database.initialize()
        return cls(database=database, storage=ManagedStorageManager(Path(root) / "managed-media"),
                   notifications=DatabaseNotificationManager(database),
                   system=RuntimeSystemManager(performance))
