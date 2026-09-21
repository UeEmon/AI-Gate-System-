"""Contracts between the replaceable AI Gate System components."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass(slots=True)
class Frame:
    image: Any
    index: int
    timestamp_ms: float | None
    source_id: str


@dataclass(slots=True)
class Detection:
    label: str
    confidence: float
    box: tuple[int, int, int, int]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OCRResult:
    text: str
    confidence: float
    fields: dict[str, str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class InputProvider(ABC):
    @abstractmethod
    def frames(self) -> Iterable[Frame]: ...


class ObjectDetector(ABC):
    @abstractmethod
    def detect(self, frame: Frame) -> list[Detection]: ...


class PlateDetector(ObjectDetector):
    pass


class OCRReader(ABC):
    @abstractmethod
    def read(self, image: Any) -> list[OCRResult]: ...


class DecisionEngine(ABC):
    @abstractmethod
    def decide(self, detection: Detection, ocr: OCRResult | None) -> dict[str, Any]: ...


class DatabaseManager(ABC):
    @abstractmethod
    def initialize(self) -> None: ...


class NotificationManager(ABC):
    @abstractmethod
    def publish(self, event: dict[str, Any]) -> None: ...


class StorageManager(ABC):
    @abstractmethod
    def store(self, category: str, content: bytes, suffix: str) -> Path: ...


class TrainingManager(ABC):
    @abstractmethod
    def train(self, samples: list[dict[str, Any]]) -> str: ...


class SystemManager(ABC):
    @abstractmethod
    def health(self) -> dict[str, Any]: ...
