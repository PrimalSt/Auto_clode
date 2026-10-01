"""История выгрузок: периоды загрузок, правила пересечения, действующая история, покрытие.
Чистые функции над манифестом истории, без доступа к хранилищу (ARCHITECTURE.md, раздел 6.3)."""

from .coverage import coverage_report, guess_period_type, overlapping_uploads, rows_outside
from .view import coverage, default_report_period, history_view, upload_period

__all__ = [
    "coverage",
    "coverage_report",
    "default_report_period",
    "guess_period_type",
    "history_view",
    "overlapping_uploads",
    "rows_outside",
    "upload_period",
]
