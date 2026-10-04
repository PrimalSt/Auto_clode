"""Папка данных приложения: источники, загрузки, история, сопоставление столбцов, сценарии,
шаблоны, запуски, резервные копии и черновики сценария (``Home``).

Пакет не импортирует модули обработки: тяжёлую работу делает исполнитель, переданный в
``Home.open(worker=…)`` (``WorkerApi``). Поэтому ``Home`` работает и в своём коде
(``autogenerator.api.Home`` — исполнитель в том же процессе), и в сервере приложения, где
исполнители — отдельные процессы.
"""

from .drafts import SlidePreview
from .home import ChooseMapping, ChoosePolicy, ColumnUsage, Home, MappingChoice, UploadOutcome
from .library import SavedScenario, ThemeImport
from .runs import BackupInfo
from .workers import WorkerApi

__all__ = [
    "BackupInfo",
    "ChooseMapping",
    "ChoosePolicy",
    "ColumnUsage",
    "Home",
    "MappingChoice",
    "SavedScenario",
    "SlidePreview",
    "ThemeImport",
    "UploadOutcome",
    "WorkerApi",
]
