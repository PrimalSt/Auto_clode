"""Движок сценария: граф узлов, обработка входов, наборы данных и показатели
(ARCHITECTURE.md, раздел 6.4).

Движок не знает, откуда берётся история: её даёт ``HistoryProvider`` из контрактов.
"""

from .analysis import InputSchema, ScenarioPlan, analyze, column_usage
from .execute import execute
from .loading import load_scenario
from .sqlexpr import SqlTranslator

__all__ = [
    "InputSchema",
    "ScenarioPlan",
    "SqlTranslator",
    "analyze",
    "column_usage",
    "execute",
    "load_scenario",
]
