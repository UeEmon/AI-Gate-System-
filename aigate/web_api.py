"""Flask API for model selection and runtime performance."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from .model_registry import ModelRegistry
from .performance import PerformanceManager
from .settings import SettingsManager


def create_system_blueprint(settings: SettingsManager, models: ModelRegistry,
                            performance: PerformanceManager) -> Blueprint:
    api = Blueprint("system_api", __name__)

    @api.get("/api/models")
    def list_models():
        return jsonify(models=models.list(), selection=settings.read())

    @api.get("/api/system/performance")
    def system_performance():
        return jsonify(startup=performance.startup_report(), runtime=performance.summary(),
                       resources=performance.current_resources(), settings=settings.read())

    @api.post("/api/system/benchmark")
    def run_benchmark():
        report = performance.startup_benchmark()
        current = settings.read()
        if current["profile"] == "auto":
            recommendation = report["recommendation"]
            available = {item["id"] for item in models.list() if item["available"]}
            if recommendation["vehicle_model"] not in available:
                recommendation["vehicle_model"] = current["vehicle_model"]
            current = settings.update({**recommendation, "profile": "auto"})
        return jsonify(startup=report, settings=current)

    @api.put("/api/settings/models")
    def select_models():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="モデル設定をJSONで指定してください。"), 400
        presets = {"speed": {"imgsz": 640, "frame_stride": 2},
                   "balanced": {"imgsz": 960, "frame_stride": 1},
                   "accuracy": {"imgsz": 1280, "frame_stride": 1}}
        if payload.get("profile") in presets:
            payload = {**payload, **presets[payload["profile"]]}
        proposed = {**settings.read(), **payload}
        try:
            models.validate_selection(proposed)
            saved = settings.update(payload)
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(settings=saved)

    return api
