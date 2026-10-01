"""Интерфейсы плагинов (ARCHITECTURE.md, раздел 4.3).

Всё, что можно расширять, устроено как плагин, включая встроенное: читатели файлов, шаги,
окна данных, агрегаты и блоки слайдов. Плагин — класс-наследник одного из базовых классов
ниже, объявленный в entry points своего пакета. Хозяева (``ingest``, ``engine``, ``render``)
получают плагины только из реестра ``plugin_host`` и не импортируют пакеты плагинов.

Версия API плагинов — единственная версия с semver-смыслом в приложении. Пока она 0.x,
несовместимым считается изменение второго числа.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

import pyarrow as pa
from pydantic import BaseModel, ConfigDict, Field

from .periods import DateSpan, Period
from .results import Issue
from .scenario import WindowSpec
from .sources import DType, ReadOptions
from .theme import Geometry

if TYPE_CHECKING:
    import polars as pl

PLUGIN_API_VERSION = "0.3"

ColumnTypes = dict[str, DType | None]
"""Столбцы таблицы и их типы; ``None`` — тип станет известен только после прогона
(например, столбец, который добавил код на Python без объявленного типа)."""


class PluginKind(StrEnum):
    READER = "reader"
    STEP = "step"
    WINDOW = "window"
    AGGREGATION = "aggregation"
    BLOCK = "block"


ENTRY_POINT_GROUPS: dict[PluginKind, str] = {
    PluginKind.READER: "autogenerator.readers",
    PluginKind.STEP: "autogenerator.steps",
    PluginKind.WINDOW: "autogenerator.windows",
    PluginKind.AGGREGATION: "autogenerator.aggregations",
    PluginKind.BLOCK: "autogenerator.blocks",
}


class NoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- База -----------------------------------------------------------------------


class Plugin(ABC):
    """Общее у всех плагинов."""

    kind: ClassVar[PluginKind]
    name: ClassVar[str]
    api_version: ClassVar[str] = PLUGIN_API_VERSION
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""


class ParamsPlugin(Plugin):
    """Плагин с параметрами. Параметры описывает Pydantic-модель ``Params``: по ней
    проверяется сценарий и строится форма настройки (JSON Schema)."""

    Params: ClassVar[type[BaseModel]] = NoParams
    type_version: ClassVar[int] = 1

    def migrate_params(self, params: dict[str, Any], from_version: int) -> dict[str, Any]:
        """Перевести параметры старой версии в текущую. По умолчанию старых версий нет."""
        raise ValueError(
            f"Плагин {self.name} не умеет переводить параметры версии {from_version} в версию {self.type_version}"
        )

    def parse_params(self, params: dict[str, Any], type_version: int = 1) -> BaseModel:
        if type_version > self.type_version:
            raise ValueError(
                f"Параметры версии {type_version} новее плагина {self.name} "
                f"(он знает версию {self.type_version}); обновите приложение"
            )
        if type_version < self.type_version:
            params = self.migrate_params(params, type_version)
        return self.Params.model_validate(params)


# --- Читатели файлов --------------------------------------------------------------


@dataclass
class ReadProgress:
    """Ход чтения файла: этап, сколько сделано и сколько всего (``None`` — неизвестно)."""

    stage: str
    done: int = 0
    total: int | None = None
    unit: str = "bytes"


ProgressCallback = Callable[[ReadProgress], None]


@dataclass
class SampleTable:
    """Выборка строк файла для вывода типов и профиля: начало, середина и конец."""

    table: pa.Table
    parts: list[str] = field(default_factory=list)
    rows_estimate: int | None = None
    notes: list[str] = field(default_factory=list)


class ReaderPlugin(Plugin):
    """Читатель формата файла. Все столбцы отдаются текстом: типы выводит и приводит
    ``ingest``, одинаково для всех форматов."""

    kind = PluginKind.READER
    formats: ClassVar[tuple[str, ...]] = ()
    sample_reads_all: ClassVar[bool] = False
    """Выборка читает файл целиком (так у Excel). Тогда при загрузке снимок строится по шапке
    (``columns``), а не по выборке, чтобы не читать файл дважды."""

    @abstractmethod
    def can_read(self, path: Path) -> bool:
        """Узнаёт ли читатель файл (по сигнатуре, а не только по расширению)."""

    @abstractmethod
    def sniff(self, path: Path, options: ReadOptions) -> ReadOptions:
        """Определить недостающие параметры чтения; заданные в ``options`` не менять.
        У Excel ``sheet`` в ответе — список листов, которые войдут в выгрузку."""

    @abstractmethod
    def batches(
        self,
        path: Path,
        options: ReadOptions,
        batch_rows: int = 100_000,
        progress: ProgressCallback | None = None,
    ) -> Iterator[pa.RecordBatch]:
        """Порции строк; все столбцы текстовые (``string``, ``large_string`` или ``string_view``),
        названия — как в файле (повторы различаются суффиксом « (2)», « (3)»). ``options`` —
        результат ``sniff``. Ошибку кодировки или формата читатель сообщает ``AgenError`` с
        номером строки файла."""

    def columns(self, path: Path, options: ReadOptions) -> list[str]:
        """Названия столбцов в том виде, в каком их отдаст ``batches``. По умолчанию — по
        выборке из первых строк; читатели с дорогой выборкой читают только шапку."""
        return list(self.sample(path, options, rows=100).table.column_names)

    def sample(self, path: Path, options: ReadOptions, rows: int = 10_000) -> SampleTable:
        """Выборка для вывода типов. По умолчанию — первые ``rows`` строк; читатели, которые
        умеют прыгать по файлу, добавляют порции из середины и конца (раздел 6.1, п. 4)."""
        got: list[pa.RecordBatch] = []
        n = 0
        for b in self.batches(path, options, batch_rows=rows):
            got.append(b.slice(0, rows - n))
            n += got[-1].num_rows
            if n >= rows:
                break
        if not got:
            return SampleTable(pa.table({}), ["начало"])
        return SampleTable(pa.Table.from_batches(got), ["начало"])


# --- Шаги обработки ----------------------------------------------------------------


class ExpressionTools(Protocol):
    """Работа с SQL (диалект DuckDB) без данных. Реализацию даёт ``engine``."""

    def expr(self, sql: str) -> pl.Expr:
        """Перевести SQL-выражение в выражение Polars. Если в нём есть функция, которой нет
        в таблице перевода, — ``AgenError`` с кодом ``expression``; при выполнении такие
        формулы считает DuckDB (``StepContext.with_column`` и ``filter``)."""
        ...

    def columns_in(self, sql: str) -> set[str]:
        """Какие столбцы использует SQL-выражение."""
        ...

    def tables_in(self, sql: str) -> set[str]:
        """Какие таблицы читает SQL-запрос (``data`` — текущая таблица, остальные — по ``id``)."""
        ...

    def query_columns(self, sql: str, table: str) -> set[str] | None:
        """Какие столбцы таблицы ``table`` использует запрос; ``None`` — все (``SELECT *``)."""
        ...


class SchemaTools(ExpressionTools, Protocol):
    """Что шаг получает при разборе сценария до чтения данных: типы столбцов."""

    def expr_type(self, sql: str, schema: ColumnTypes) -> DType | None:
        """Тип результата SQL-выражения над таблицей со столбцами ``schema``; ``None`` —
        не определить без данных. Ошибка в формуле — ``AgenError``."""
        ...

    def query_schema(self, sql: str, tables: Mapping[str, ColumnTypes]) -> ColumnTypes | None:
        """Столбцы и типы результата SQL-запроса. ``tables`` — текущая таблица (``data``);
        таблицы других входов сценария движок подставляет сам. ``None`` — не определить без
        прогона (нужна таблица, столбцы которой известны только после него). Ошибка —
        ``AgenError``."""
        ...

    def input_schema(self, input_id: str) -> ColumnTypes | None:
        """Столбцы другого входа сценария после его обработки; ``None`` — входа нет или его
        столбцы станут известны только после прогона."""
        ...


CodeMode = Literal["table", "lazy", "batches"]
CodeFrame = Literal["pandas", "polars"]
PandasTypes = Literal["numpy", "arrow"]


class StepContext(ExpressionTools, Protocol):
    """Что шаг получает при выполнении."""

    input_id: str
    step_id: str
    period: Period
    period_column: str
    anchor: date
    """Последний день, от которого отсчитываются относительные периоды: конец отчётного
    периода или дата запуска (настройка сценария ``relative_to``)."""
    large: bool
    """Данных много: операции, которым нужна вся таблица (удаление дубликатов, сортировка,
    объединение), выгоднее отдать DuckDB (``sql``) с выгрузкой на диск."""
    preview: bool
    """Шаг выполняется для превью, часто на выборке: предупреждения о данных не выдаются."""

    def window(self, spec: WindowSpec | str) -> DateSpan: ...

    def warn(self, message: str) -> None: ...

    def log(self, text: str) -> None:
        """Строка в журнал запуска (например, вывод ``print`` пользовательского кода)."""
        ...

    def with_column(self, lf: pl.LazyFrame, name: str, sql: str) -> pl.LazyFrame:
        """Добавить или заменить столбец по SQL-формуле: в Polars, а если формулу не
        перевести — в DuckDB с тем же результатом."""
        ...

    def filter(self, lf: pl.LazyFrame, sql: str) -> pl.LazyFrame:
        """Оставить строки, где условие истинно (пусто — ложно, как в SQL)."""
        ...

    def input(self, input_id: str) -> pl.LazyFrame:
        """Результат обработки другого входа сценария (из ``inputs_used``)."""
        ...

    def sql(self, query: str, tables: Mapping[str, pl.LazyFrame]) -> pl.LazyFrame:
        """Выполнить запрос в DuckDB. Таблицы передаются через Parquet на диске, DuckDB
        считает с лимитом памяти и выгрузкой на диск, результат — ленивая таблица над Parquet."""
        ...

    def run_code(
        self,
        lf: pl.LazyFrame,
        code: str,
        *,
        mode: CodeMode = "table",
        frame: CodeFrame = "pandas",
        function: str = "transform",
        timeout: float | None = None,
        pandas_types: PandasTypes = "numpy",
    ) -> pl.LazyFrame:
        """Выполнить пользовательский код на Python: ``table`` и ``batches`` — в отдельном
        процессе с таймаутом и лимитом памяти, ``lazy`` — над ленивой таблицей Polars.
        ``pandas_types`` — типы столбцов pandas: привычные numpy или Arrow (экономнее по
        памяти, но не все методы pandas с ними работают)."""
        ...


class StepPlugin(ParamsPlugin):
    """Шаг обработки входа: ленивая таблица → ленивая таблица."""

    kind = PluginKind.STEP
    row_local: ClassVar[bool] = True
    """Построчный шаг: результат строки не зависит от других строк, поэтому нижнюю границу
    истории можно протолкнуть через него (раздел 6.4)."""

    def lookback(self, params: Any) -> int | None:
        """Сколько периодов истории до нижней границы нужно шагу: 0 — построчный шаг,
        ``None`` — вся история (граница не проталкивается). Удаление дубликатов с глубиной
        поиска N возвращает N."""
        return 0 if self.row_local else None

    def inputs_used(self, params: Any, tools: ExpressionTools) -> list[str]:
        """Другие входы сценария, которые нужны шагу (объединение, SQL)."""
        return []

    @abstractmethod
    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        """Столбцы, которые шаг читает. По ним строится сверка структуры."""

    def columns_mentioned(self, params: Any) -> set[str]:
        """Слабая связь: слова, которые могут быть столбцами (строки в коде на Python).
        Если такого столбца нет в выгрузке, это предупреждение, а не блокировка."""
        return set()

    def reads_all_columns(self, params: Any, tools: ExpressionTools) -> bool:
        """Нужны ли шагу все столбцы входа (код без ``uses``, ``SELECT *``)."""
        return False

    def key_columns(self, params: Any) -> dict[str, list[str]]:
        """Столбцы, строки с одинаковыми значениями которых шаг сравнивает между собой:
        ``data`` — ключ текущей таблицы (дубликаты, объединение), другие входы — по ``id``
        (ключ второй стороны объединения). Превью на выборке берёт строки по хешу этих
        столбцов, чтобы дубликаты и пары ключей попадали в выборку вместе."""
        return {}

    def cacheable(self, params: Any) -> bool:
        """Можно ли кэшировать результат шага. Код на Python, который читает что-то кроме
        своих входов (файлы, сеть, текущее время), объявляет ``cache: false``."""
        return True

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        """Столбцы и типы после шага; ``None`` — станут известны только после прогона.
        По умолчанию шаг их не меняет."""
        return schema

    def check(self, params: Any, tools: ExpressionTools) -> list[Issue]:
        """Проверка параметров без данных сверх модели ``Params``: синтаксис кода, опасные
        вызовы и т. п. Ошибки (``level=error``) не дают запустить сценарий."""
        return []

    @abstractmethod
    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame: ...


# --- Окна данных и агрегаты ----------------------------------------------------------


class WindowPlugin(ParamsPlugin):
    """Окно данных: отрезок истории относительно отчётного периода."""

    kind = PluginKind.WINDOW

    @abstractmethod
    def resolve(self, period: Period, params: Any) -> DateSpan: ...


class AggregationPlugin(Plugin):
    """Агрегат для наборов и показателей: выражение Polars и эквивалент на SQL."""

    kind = PluginKind.AGGREGATION
    needs_column: ClassVar[bool] = True
    needs_order: ClassVar[bool] = False
    """Результат зависит от порядка строк (первое и последнее значение): движок сортирует
    строки по столбцу периода и порядку загрузки перед агрегатом."""

    @abstractmethod
    def polars_expr(self, column: str | None) -> pl.Expr: ...

    @abstractmethod
    def sql(self, column: str | None) -> str: ...


# --- Блоки слайдов ---------------------------------------------------------------


class DataNeeds(BaseModel):
    """Какие наборы и показатели нужны блоку. По ним строятся рёбра графа."""

    datasets: set[str] = Field(default_factory=set)
    metrics: set[str] = Field(default_factory=set)


@dataclass
class BlockTarget:
    """Куда ставится блок. ``slide`` и ``placeholder`` — объекты python-pptx."""

    slide: Any
    geometry: Geometry
    placeholder: Any | None = None
    slot: str | None = None


@dataclass
class BlockData:
    datasets: dict[str, pa.Table] = field(default_factory=dict)
    metrics: dict[str, float | int | None] = field(default_factory=dict)


class BlockContext(Protocol):
    period: Period
    scenario_name: str

    def warn(self, message: str) -> None: ...


class BlockPlugin(ParamsPlugin):
    """Блок слайда: текст, график, таблица и т. д."""

    kind = PluginKind.BLOCK

    @abstractmethod
    def data_needs(self, params: Any) -> DataNeeds: ...

    @abstractmethod
    def render(self, target: BlockTarget, params: Any, data: BlockData, ctx: BlockContext) -> None: ...


# --- Манифест плагинов ----------------------------------------------------------------


class PluginStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class PluginInfo(BaseModel):
    """Запись манифеста: по нему сервер и экран «Модули» узнают о плагинах, не загружая их."""

    kind: PluginKind
    name: str
    entry_point: str
    distribution: str | None = None
    version: str | None = None
    status: PluginStatus
    error: str | None = None
    title: str = ""
    type_version: int | None = None
    params_schema: dict[str, Any] | None = None


class PluginManifest(BaseModel):
    api_version: str = PLUGIN_API_VERSION
    plugins: list[PluginInfo] = Field(default_factory=list)

    def broken(self) -> list[PluginInfo]:
        return [p for p in self.plugins if p.status != PluginStatus.OK]
