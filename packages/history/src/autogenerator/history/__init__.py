"""История выгрузок: периоды загрузок, правила пересечения, действующая история, покрытие.
Чистые функции над манифестом истории, без доступа к хранилищу (ARCHITECTURE.md, раздел 6.3)."""

from .view import coverage, default_report_period, history_view, upload_period

__all__ = ["coverage", "default_report_period", "history_view", "upload_period"]
