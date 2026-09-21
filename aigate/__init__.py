"""AI Gate System application services.

The package keeps hardware input, inference, decisions, persistence, delivery,
training and runtime management behind explicit interfaces.  Legacy entry
points import these services so existing deployments remain compatible.
"""

from .model_registry import ModelRegistry, ModelRole, ModelSpec
from .performance import PerformanceManager
from .settings import SettingsManager

__all__ = ["ModelRegistry", "ModelRole", "ModelSpec", "PerformanceManager", "SettingsManager"]
