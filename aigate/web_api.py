"""Flask API for runtime performance."""
from __future__ import annotations

from flask import Blueprint, jsonify, abort

from .performance import PerformanceManager
from .settings import SettingsManager


def create_system_blueprint(settings: SettingsManager,
                            performance: PerformanceManager, optimization=None) -> Blueprint:
    api = Blueprint("system_api", __name__)

    @api.get("/api/system/performance")
    def system_performance():
        return jsonify(startup=performance.startup_report(), runtime=performance.summary(),
                       resources=performance.current_resources(), settings=settings.read(),
                       optimization=optimization.status() if optimization else {'state':'unavailable'})

    @api.post("/api/system/benchmark")
    def run_benchmark():
        if optimization is None:
            abort(503, description='実映像の性能測定環境がありません。')
        try:
            return jsonify(optimization.start()), 202
        except RuntimeError as error:
            abort(409, description=str(error))
        except ValueError as error:
            abort(400, description=str(error))


    return api
