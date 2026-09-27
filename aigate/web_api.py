"""Flask API for runtime performance."""
from __future__ import annotations

from flask import Blueprint, jsonify

from .performance import PerformanceManager
from .settings import SettingsManager


def create_system_blueprint(settings: SettingsManager,
                            performance: PerformanceManager) -> Blueprint:
    api = Blueprint("system_api", __name__)

    @api.get("/api/system/performance")
    def system_performance():
        return jsonify(startup=performance.startup_report(), runtime=performance.summary(),
                       resources=performance.current_resources(), settings=settings.read())

    @api.post("/api/system/benchmark")
    def run_benchmark():
        report = performance.startup_benchmark()
        current = settings.read()
        if current["profile"] == "auto":
            current = settings.update({**report["recommendation"], "profile": "auto"})
        return jsonify(startup=report, settings=current)

    return api
