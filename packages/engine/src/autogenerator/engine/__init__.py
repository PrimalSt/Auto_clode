"""Движок сценария: граф узлов, обработка входов, наборы данных и показатели, превью
(ARCHITECTURE.md, раздел 6.4).

Движок не знает, откуда берётся история: её даёт ``HistoryProvider`` из контрактов.
"""

from .analysis import InputSchema, ScenarioPlan, analyze, column_usage
from .cache import NodeCache
from .execute import EngineOptions, execute
from .loading import load_scenario
from .preview import preview, resolve_target
from .resources import Limits
from .sqlexpr import SqlTranslator

__all__ = [
    "EngineOptions",
    "InputSchema",
    "Limits",
    "NodeCache",
    "ScenarioPlan",
    "SqlTranslator",
    "analyze",
    "column_usage",
    "execute",
    "load_scenario",
    "preview",
    "resolve_target",
]
