"""Единый диалект формул: SQL-выражения DuckDB → выражения Polars (ARCHITECTURE.md, раздел 6.4).

Формула разбирается sqlglot и переводится по таблице поддерживаемых конструкций с той же
семантикой, что у DuckDB: деление целых даёт дробь, ``%`` и ``//`` округляют к нулю, ``ROUND``
— от нуля, ``DATE_TRUNC`` возвращает дату и время. Если конструкции нет в таблице, бросается
``UnsupportedExpression``: при выполнении такую формулу считает DuckDB (модуль ``duck``).

Деление на ноль даёт пустое значение (NULL), а не бесконечность: показатель «нет данных»
лучше, чем «∞ %». Для DuckDB формула переписывается так же (``to_duckdb``).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from typing import Any

import polars as pl
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from autogenerator.contracts import AgenError, ErrorCode

_INT = pl.Int64()
_FLOAT = pl.Float64()

_CAST_TYPES: dict[str, pl.DataType] = {
    "INT": _INT,
    "INTEGER": _INT,
    "BIGINT": _INT,
    "SMALLINT": _INT,
    "TINYINT": _INT,
    "HUGEINT": _INT,
    "UBIGINT": _INT,
    "DOUBLE": _FLOAT,
    "FLOAT": _FLOAT,
    "DECIMAL": _FLOAT,
    "REAL": _FLOAT,
    "NUMERIC": _FLOAT,
    "VARCHAR": pl.String(),
    "TEXT": pl.String(),
    "CHAR": pl.String(),
    "STRING": pl.String(),
    "DATE": pl.Date(),
    "TIMESTAMP": pl.Datetime("us"),
    "TIMESTAMPNTZ": pl.Datetime("us"),
    "DATETIME": pl.Datetime("us"),
    "BOOLEAN": pl.Boolean(),
    "BOOL": pl.Boolean(),
}

_TRUNC_UNITS = {
    "YEAR": "1y",
    "QUARTER": "1q",
    "MONTH": "1mo",
    "WEEK": "1w",
    "DAY": "1d",
    "HOUR": "1h",
    "MINUTE": "1m",
    "SECOND": "1s",
}

_DATE_PARTS: dict[str, Callable[[pl.Expr], pl.Expr]] = {
    "YEAR": lambda e: e.dt.year(),
    "QUARTER": lambda e: e.dt.quarter(),
    "MONTH": lambda e: e.dt.month(),
    "DAY": lambda e: e.dt.day(),
    "WEEK": lambda e: e.dt.week(),
    "DOW": lambda e: e.dt.weekday() % 7,  # воскресенье — 0, как в DuckDB
    "DAYOFWEEK": lambda e: e.dt.weekday() % 7,
    "ISODOW": lambda e: e.dt.weekday(),
    "DOY": lambda e: e.dt.ordinal_day(),
    "DAYOFYEAR": lambda e: e.dt.ordinal_day(),
    "HOUR": lambda e: e.dt.hour(),
    "MINUTE": lambda e: e.dt.minute(),
    "SECOND": lambda e: e.dt.second(),
}

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?")


class UnsupportedExpression(AgenError):
    """В формуле есть конструкция, которой нет в таблице перевода в Polars. Формула при этом
    правильная: её посчитает DuckDB."""

    def __init__(self, message: str):
        super().__init__(ErrorCode.EXPRESSION, message, details={"unsupported": True})


def _like_to_regex(pattern: str) -> str:
    out = []
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


def _short(e: ParseError) -> str:
    errs = getattr(e, "errors", None) or []
    if errs:
        first = errs[0]
        return f"{first.get('description', 'синтаксическая ошибка')} (позиция {first.get('col', '?')})"
    return str(e).splitlines()[0]


def parse_query(sql: str) -> exp.Query:
    """Разобрать SQL-запрос (``SELECT``, в том числе с ``WITH``)."""
    text = (sql or "").strip().rstrip(";")
    if not text:
        raise AgenError(ErrorCode.EXPRESSION, "Пустой запрос")
    try:
        tree = sqlglot.parse_one(text, dialect="duckdb")
    except ParseError as e:
        raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в запросе: {_short(e)}") from e
    if not isinstance(tree, exp.Query):
        raise AgenError(ErrorCode.EXPRESSION, "Нужен запрос SELECT … FROM …")
    return tree


def tables_in(sql: str) -> set[str]:
    """Таблицы, которые читает запрос (без имён из ``WITH``)."""
    tree = parse_query(sql)
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes}


def query_columns(sql: str, table: str, schemas: Mapping[str, Iterable[str]] | None = None) -> set[str] | None:
    """Столбцы таблицы ``table``, которые использует запрос; ``None`` — все (``*``) или не
    удалось разобрать без данных."""
    tree = parse_query(sql)
    schema: dict[str, object] = {t: {c: "VARCHAR" for c in cols} for t, cols in (schemas or {}).items()}
    try:
        tree = qualify(
            tree,
            schema=schema or None,
            dialect="duckdb",
            expand_stars=False,
            validate_qualify_columns=False,
            identify=False,
        )
        scopes = traverse_scope(tree)
    except Exception:
        return None
    used: set[str] = set()
    for scope in scopes:
        reads = {alias for alias, src in scope.sources.items() if isinstance(src, exp.Table) and src.name == table}
        if not reads:
            continue
        for star in scope.expression.find_all(exp.Star):
            parent = star.parent
            if isinstance(parent, exp.Column) and parent.table and parent.table not in reads:
                continue
            return None
        for col in scope.columns:
            if col.table in reads or (not col.table and len(scope.sources) == 1):
                used.add(col.name)
    return used


def to_duckdb(sql: str) -> str:
    """Формула для DuckDB с той же семантикой, что у перевода в Polars: деление на ноль — NULL."""
    tree = sqlglot.parse_one(sql, dialect="duckdb")

    def fix(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Div):
            return exp.Div(
                this=node.this, expression=exp.Nullif(this=node.expression, expression=exp.Literal.number(0))
            )
        return node

    return tree.transform(fix).sql(dialect="duckdb")


def query_to_duckdb(sql: str) -> str:
    """Запрос для DuckDB: деление на ноль — NULL, как в формулах."""
    tree = parse_query(sql)

    def fix(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Div):
            return exp.Div(
                this=node.this, expression=exp.Nullif(this=node.expression, expression=exp.Literal.number(0))
            )
        return node

    return tree.transform(fix).sql(dialect="duckdb")


class SqlTranslator:
    """Переводчик формул.

    ``columns`` — допустимые столбцы: имена или словарь «имя → тип Polars» (``None`` — не
    проверять). Типы нужны, чтобы строка ``'2026-01-01'`` в сравнении со столбцом дат стала
    датой, как в DuckDB.
    """

    def __init__(self, columns: Iterable[str] | Mapping[str, Any] | None = None) -> None:
        if columns is None:
            self.columns: set[str] | None = None
            self.types: dict[str, Any] = {}
        elif isinstance(columns, Mapping):
            self.columns = set(columns)
            self.types = dict(columns)
        else:
            self.columns = set(columns)
            self.types = {}
        self._sql = ""

    # --- публичное -------------------------------------------------------------

    def parse(self, sql: str) -> exp.Expression:
        text = (sql or "").strip()
        if not text:
            raise AgenError(ErrorCode.EXPRESSION, "Пустая формула")
        try:
            tree = sqlglot.parse_one(text, dialect="duckdb")
        except ParseError as e:
            raise AgenError(ErrorCode.EXPRESSION, f"Ошибка в формуле «{text}»: {_short(e)}") from e
        if (
            tree is None
            or isinstance(tree, exp.Query | exp.Insert | exp.Command | exp.Update | exp.Delete)
            or not isinstance(tree, exp.Expression)
        ):
            raise AgenError(ErrorCode.EXPRESSION, f"«{text}» — не выражение, а запрос")
        return tree

    def columns_in(self, sql: str) -> set[str]:
        return {c.name for c in self.parse(sql).find_all(exp.Column)}

    def tables_in(self, sql: str) -> set[str]:
        return tables_in(sql)

    def query_columns(self, sql: str, table: str) -> set[str] | None:
        return query_columns(sql, table)

    def expr(self, sql: str) -> pl.Expr:
        self._sql = sql
        tree = self.parse(sql)
        if self.columns is not None:
            for c in tree.find_all(exp.Column):
                if c.name not in self.columns:
                    known = ", ".join(sorted(x for x in self.columns if not x.startswith("_")))
                    raise self._fail(f"нет столбца «{c.name}». Есть: {known}")
        return self._tr(tree)

    def supports(self, sql: str) -> bool:
        """Переводится ли формула в Polars целиком."""
        try:
            self.expr(sql)
        except UnsupportedExpression:
            return False
        return True

    # --- перевод ---------------------------------------------------------------

    def _fail(self, what: str) -> AgenError:
        return AgenError(ErrorCode.EXPRESSION, f"В формуле «{self._sql}» {what}")

    def _unsupported(self, what: str) -> UnsupportedExpression:
        return UnsupportedExpression(f"В формуле «{self._sql}» {what}")

    def _tr(self, n: exp.Expression) -> pl.Expr:
        handler = _HANDLERS.get(type(n))
        if handler is not None:
            return handler(self, n)
        if isinstance(n, exp.Anonymous | exp.Func):
            name = n.sql_name() if not isinstance(n, exp.Anonymous) else str(n.this)
            raise self._unsupported(f"функция {name.upper()} переводится в DuckDB")
        raise self._unsupported(f"конструкция «{n.sql(dialect='duckdb')}» переводится в DuckDB")

    def _column(self, n: exp.Column) -> pl.Expr:
        if isinstance(n.this, exp.Star):
            raise self._fail("* можно использовать только в запросе")
        return pl.col(n.name)

    def _literal(self, n: exp.Literal) -> pl.Expr:
        if n.is_string:
            return pl.lit(n.this)
        text = str(n.this)
        return pl.lit(int(text)) if re.fullmatch(r"-?\d+", text) else pl.lit(float(text))

    def _const(self, n: exp.Expression) -> Any:
        if isinstance(n, exp.Literal):
            if n.is_string:
                return n.this
            t = str(n.this)
            return int(t) if re.fullmatch(r"-?\d+", t) else float(t)
        if isinstance(n, exp.Neg) and isinstance(n.this, exp.Literal):
            return -self._const(n.this)
        if isinstance(n, exp.Boolean):
            return bool(n.this)
        if isinstance(n, exp.Null):
            return None
        if isinstance(n, exp.Paren):
            return self._const(n.this)
        raise self._fail(f"ожидалось значение, а не «{n.sql()}»")

    def _dtype_of(self, n: exp.Expression) -> Any:
        if isinstance(n, exp.Column):
            return self.types.get(n.name)
        return None

    def _typed(self, n: exp.Expression, other: exp.Expression) -> pl.Expr:
        """Строковая константа рядом со столбцом дат становится датой, как в DuckDB."""
        if isinstance(n, exp.Literal) and n.is_string:
            dt = self._dtype_of(other)
            if dt is not None:
                return pl.lit(self._coerce(n.this, dt))
        return self._tr(n)

    def _coerce(self, value: Any, dtype: Any) -> Any:
        if not isinstance(value, str):
            return value
        if dtype == pl.Date and _ISO_DATE.fullmatch(value):
            return date.fromisoformat(value)
        if isinstance(dtype, pl.Datetime) and (_ISO_DATE.fullmatch(value) or _ISO_DATETIME.fullmatch(value)):
            return datetime.fromisoformat(value)
        return value

    def _div(self, n: exp.Div) -> pl.Expr:
        a, b = self._tr(n.this), self._tr(n.expression)
        return pl.when(b != 0).then(a.cast(_FLOAT) / b).otherwise(None)

    def _intdiv(self, n: exp.IntDiv) -> pl.Expr:
        a, b = self._tr(n.this), self._tr(n.expression)
        return pl.when(b != 0).then((a / b).cast(_INT)).otherwise(None)  # к нулю, как в DuckDB

    def _mod(self, n: exp.Mod) -> pl.Expr:
        a, b = self._tr(n.this), self._tr(n.expression)
        r = a % b  # в Polars знак — как у делителя, в DuckDB — как у делимого
        fixed = pl.when((r != 0) & ((a < 0) != (b < 0))).then(r - b).otherwise(r)
        return pl.when(b != 0).then(fixed).otherwise(None)

    def _is(self, n: exp.Is) -> pl.Expr:
        if isinstance(n.expression, exp.Null):
            return self._tr(n.this).is_null()
        if isinstance(n.expression, exp.Boolean):
            v = self._tr(n.this)
            return v.fill_null(not n.expression.this) if n.expression.this else ~v.fill_null(True)
        raise self._unsupported("IS поддерживается только с NULL, TRUE и FALSE")

    def _in(self, n: exp.In) -> pl.Expr:
        if n.args.get("query") is not None:
            raise self._unsupported("подзапросы в IN считает DuckDB")
        dt = self._dtype_of(n.this)
        values = [self._coerce(self._const(v), dt) if dt is not None else self._const(v) for v in n.expressions]
        col = self._tr(n.this)
        has_null = any(v is None for v in values)
        values = [v for v in values if v is not None]
        res = col.is_in(values)
        # Как в SQL: x IN (…) для пустого x — пусто; если в списке NULL, «не найдено» — пусто.
        res = pl.when(col.is_null()).then(None).otherwise(res)
        if has_null:
            res = pl.when(res).then(True).otherwise(None)
        return ~res if n.args.get("negate") else res

    def _between(self, n: exp.Between) -> pl.Expr:
        v = self._tr(n.this)
        res = v.is_between(self._typed(n.args["low"], n.this), self._typed(n.args["high"], n.this))
        return ~res if n.args.get("negate") else res

    def _like(self, n: exp.Like | exp.ILike) -> pl.Expr:
        pattern = n.expression
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            raise self._unsupported("шаблон LIKE не строка")
        if n.args.get("escape") is not None:
            raise self._unsupported("LIKE … ESCAPE считает DuckDB")
        rx = ("(?is)" if isinstance(n, exp.ILike) else "(?s)") + _like_to_regex(pattern.this)
        res = self._tr(n.this).str.contains(rx)
        return ~res if n.args.get("negate") else res

    def _str_arg(self, n: exp.Expression | None, what: str) -> str:
        if not (isinstance(n, exp.Literal) and n.is_string):
            raise self._unsupported(f"{what}: нужна строка-константа")
        return str(n.this)

    def _int_arg(self, n: exp.Expression | None, what: str) -> int:
        try:
            v = self._const(n) if n is not None else None
        except AgenError:
            v = None
        if not isinstance(v, int):
            raise self._unsupported(f"{what}: нужно целое-константа")
        return v

    def _case(self, n: exp.Case) -> pl.Expr:
        ifs = n.args.get("ifs") or []
        if not ifs:
            raise self._fail("CASE без WHEN")
        subject = n.this
        chain: Any = None
        for i in ifs:
            cond = self._tr(i.this) if subject is None else (self._tr(subject) == self._typed(i.this, subject))
            val = self._tr(i.args["true"])
            chain = pl.when(cond).then(val) if chain is None else chain.when(cond).then(val)
        default = n.args.get("default")
        return chain.otherwise(self._tr(default) if default is not None else None)

    def _if(self, n: exp.If) -> pl.Expr:
        false = n.args.get("false")
        return (
            pl.when(self._tr(n.this))
            .then(self._tr(n.args["true"]))
            .otherwise(self._tr(false) if false is not None else None)
        )

    def _coalesce(self, n: exp.Coalesce) -> pl.Expr:
        return pl.coalesce([self._tr(n.this), *(self._tr(e) for e in n.expressions)])

    def _round(self, n: exp.Round) -> pl.Expr:
        d = n.args.get("decimals")
        digits = self._int_arg(d, "ROUND") if d is not None else 0
        if digits < 0:
            raise self._unsupported("ROUND с отрицательным числом знаков считает DuckDB")
        return self._tr(n.this).cast(_FLOAT).round(digits, mode="half_away_from_zero")

    def _cast(self, n: exp.Cast) -> pl.Expr:
        to = n.to.this.name if hasattr(n.to.this, "name") else str(n.to.this)
        target = _CAST_TYPES.get(str(to).upper())
        if target is None:
            raise self._unsupported(f"приведение к типу {to} считает DuckDB")
        strict = not isinstance(n, exp.TryCast)
        inner = n.this
        if isinstance(inner, exp.Literal) and inner.is_string:
            try:
                if target == pl.Date:
                    return pl.lit(date.fromisoformat(inner.this))
                if isinstance(target, pl.Datetime):
                    return pl.lit(datetime.fromisoformat(inner.this))
            except ValueError as e:
                if strict:
                    raise self._fail(f"«{inner.this}» — не дата") from e
                return pl.lit(None, dtype=target)
        v = self._tr(inner)
        if target == _INT:
            # DuckDB округляет дробь при приведении к целому, Polars отбрасывает дробную часть.
            src = self._dtype_of(inner)
            if src is not None and src.is_integer():
                return v.cast(_INT, strict=strict)
            # Как в DuckDB: половина округляется к чётному (2,5 → 2, 3,5 → 4).
            return v.cast(_FLOAT, strict=strict).round(0, mode="half_to_even").cast(_INT, strict=strict)
        return v.cast(target, strict=strict)

    def _concat(self, n: exp.DPipe) -> pl.Expr:
        return pl.concat_str([self._tr(n.this).cast(pl.String), self._tr(n.expression).cast(pl.String)])

    def _concat_fn(self, n: exp.Concat) -> pl.Expr:
        parts = [self._tr(e).cast(pl.String) for e in n.expressions]
        return pl.concat_str(parts, ignore_nulls=True)

    def _trunc_date(self, n: exp.TimestampTrunc | exp.DateTrunc) -> pl.Expr:
        unit_node = n.args.get("unit")
        unit = (unit_node.name if unit_node is not None else "").upper()
        every = _TRUNC_UNITS.get(unit)
        if every is None:
            raise self._unsupported(f"DATE_TRUNC('{unit.lower()}') считает DuckDB")
        # В DuckDB результат DATE_TRUNC — дата и время, даже для столбца дат.
        return self._tr(n.this).cast(pl.Datetime("us")).dt.truncate(every)

    def _extract(self, n: exp.Extract) -> pl.Expr:
        part = n.this.name.upper() if isinstance(n.this, exp.Var | exp.Identifier) else str(n.this).upper()
        fn = _DATE_PARTS.get(part)
        if fn is None:
            raise self._unsupported(f"EXTRACT({part}) считает DuckDB")
        return fn(self._tr(n.expression)).cast(_INT)

    def _strftime(self, n: exp.TimeToStr) -> pl.Expr:
        fmt = self._str_arg(n.args.get("format"), "STRFTIME")
        if re.search(r"%[^YmdHMSjyBbAaFTe%]", fmt):
            raise self._unsupported(f"формат «{fmt}» считает DuckDB")
        return self._tr(n.this).dt.strftime(fmt)

    def _strptime(self, n: exp.StrToTime) -> pl.Expr:
        fmt = self._str_arg(n.args.get("format"), "STRPTIME")
        return self._tr(n.this).str.strptime(pl.Datetime("us"), fmt, strict=True)

    def _datediff(self, n: exp.DateDiff) -> pl.Expr:
        unit_node = n.args.get("unit")
        unit = (unit_node.name if unit_node is not None else "DAY").upper()
        # sqlglot: DATE_DIFF(unit, a, b) → this=b, expression=a; результат — b − a.
        b, a = self._tr(n.this), self._tr(n.expression)
        if unit == "DAY":
            return (b.cast(pl.Date) - a.cast(pl.Date)).dt.total_days().cast(_INT)
        months = (b.dt.year() * 12 + b.dt.month()).cast(_INT) - (a.dt.year() * 12 + a.dt.month()).cast(_INT)
        if unit == "MONTH":
            return months
        if unit == "QUARTER":
            return (b.dt.year() * 4 + b.dt.quarter()).cast(_INT) - (a.dt.year() * 4 + a.dt.quarter()).cast(_INT)
        if unit == "YEAR":
            return (b.dt.year() - a.dt.year()).cast(_INT)
        raise self._unsupported(f"DATE_DIFF('{unit.lower()}') считает DuckDB")

    def _make_date(self, n: exp.DateFromParts) -> pl.Expr:
        return pl.date(self._tr(n.args["year"]), self._tr(n.args["month"]), self._tr(n.args["day"]))

    def _last_day(self, n: exp.LastDay) -> pl.Expr:
        if n.args.get("unit") is not None:
            raise self._unsupported("LAST_DAY с единицей считает DuckDB")
        return self._tr(n.this).dt.month_end().cast(pl.Date)

    def _substring(self, n: exp.Substring) -> pl.Expr:
        s = self._tr(n.this)
        start = self._int_arg(n.args.get("start"), "SUBSTRING")
        length = n.args.get("length")
        if start < 1:
            raise self._unsupported("SUBSTRING с началом меньше 1 считает DuckDB")
        ln = self._int_arg(length, "SUBSTRING") if length is not None else None
        return s.str.slice(start - 1, ln)

    def _left(self, n: exp.Left) -> pl.Expr:
        k = self._int_arg(n.expression, "LEFT")
        if k < 0:
            raise self._unsupported("LEFT с отрицательной длиной считает DuckDB")
        return self._tr(n.this).str.head(k)

    def _right(self, n: exp.Right) -> pl.Expr:
        k = self._int_arg(n.expression, "RIGHT")
        if k < 0:
            raise self._unsupported("RIGHT с отрицательной длиной считает DuckDB")
        return self._tr(n.this).str.tail(k)

    def _replace(self, n: exp.Replace) -> pl.Expr:
        old = self._str_arg(n.expression, "REPLACE")
        new = self._str_arg(n.args.get("replacement"), "REPLACE")
        return self._tr(n.this).str.replace_all(old, new, literal=True)

    def _regexp_like(self, n: exp.RegexpLike) -> pl.Expr:
        if n.args.get("flag") is not None:
            raise self._unsupported("REGEXP_MATCHES с флагами считает DuckDB")
        return self._tr(n.this).str.contains(self._str_arg(n.expression, "REGEXP_MATCHES"))

    def _regexp_full(self, n: exp.RegexpFullMatch) -> pl.Expr:
        rx = self._str_arg(n.expression, "REGEXP_FULL_MATCH")
        return self._tr(n.this).str.contains(f"^(?:{rx})$")

    def _regexp_replace(self, n: exp.RegexpReplace) -> pl.Expr:
        rx = self._str_arg(n.expression, "REGEXP_REPLACE")
        repl = self._str_arg(n.args.get("replacement"), "REGEXP_REPLACE")
        mod = n.args.get("modifiers")
        flags = self._str_arg(mod, "REGEXP_REPLACE") if mod is not None else ""
        if set(flags) - {"g"}:
            raise self._unsupported("REGEXP_REPLACE с флагами, кроме 'g', считает DuckDB")
        repl = re.sub(r"\\(\d)", r"${\1}", repl)
        s = self._tr(n.this)
        return s.str.replace_all(rx, repl) if "g" in flags else s.str.replace(rx, repl, n=1)

    def _regexp_extract(self, n: exp.RegexpExtract) -> pl.Expr:
        rx = self._str_arg(n.expression, "REGEXP_EXTRACT")
        group = n.args.get("group")
        g = self._int_arg(group, "REGEXP_EXTRACT") if group is not None else 0
        s = self._tr(n.this)
        # В DuckDB «не нашлось» — пустая строка, а не NULL.
        return pl.when(s.is_null()).then(None).otherwise(s.str.extract(rx, g).fill_null(""))

    def _split_part(self, n: exp.SplitPart) -> pl.Expr:
        sep = self._str_arg(n.args.get("delimiter"), "SPLIT_PART")
        idx = self._int_arg(n.args.get("part_index"), "SPLIT_PART")
        if idx < 1 or not sep:
            raise self._unsupported("SPLIT_PART с таким номером считает DuckDB")
        s = self._tr(n.this)
        return (
            pl.when(s.is_null())
            .then(None)
            .otherwise(s.str.split(sep).list.get(idx - 1, null_on_oob=True).fill_null(""))
        )

    def _contains(self, n: exp.Contains) -> pl.Expr:
        return self._tr(n.this).str.contains(self._str_arg(n.expression, "CONTAINS"), literal=True)

    def _starts(self, n: exp.StartsWith) -> pl.Expr:
        return self._tr(n.this).str.starts_with(self._str_arg(n.expression, "STARTS_WITH"))

    def _ends(self, n: exp.EndsWith) -> pl.Expr:
        return self._tr(n.this).str.ends_with(self._str_arg(n.expression, "ENDS_WITH"))

    def _trim(self, n: exp.Trim) -> pl.Expr:
        if n.expression is not None:
            raise self._unsupported("TRIM с набором символов считает DuckDB")
        pos = (n.args.get("position") or "").upper()
        s = self._tr(n.this)
        if pos == "LEADING":
            return s.str.strip_chars_start(" ")
        if pos == "TRAILING":
            return s.str.strip_chars_end(" ")
        return s.str.strip_chars(" ")

    def _pad(self, n: exp.Pad) -> pl.Expr:
        width = self._int_arg(n.expression, "LPAD")
        fill = self._str_arg(n.args.get("fill_pattern"), "LPAD")
        if len(fill) != 1:
            raise self._unsupported("LPAD и RPAD с заполнителем длиннее одного символа считает DuckDB")
        s = self._tr(n.this)
        if n.args.get("is_left"):
            return pl.when(s.str.len_chars() >= width).then(s.str.head(width)).otherwise(s.str.pad_start(width, fill))
        return pl.when(s.str.len_chars() >= width).then(s.str.head(width)).otherwise(s.str.pad_end(width, fill))

    def _nullif(self, n: exp.Nullif) -> pl.Expr:
        a = self._tr(n.this)
        return pl.when(a == self._typed(n.expression, n.this)).then(None).otherwise(a)

    def _greatest(self, n: exp.Greatest) -> pl.Expr:
        return pl.max_horizontal([self._tr(n.this), *(self._tr(e) for e in n.expressions)])

    def _least(self, n: exp.Least) -> pl.Expr:
        return pl.min_horizontal([self._tr(n.this), *(self._tr(e) for e in n.expressions)])

    def _log(self, n: exp.Log) -> pl.Expr:
        if n.expression is None:
            return self._tr(n.this).cast(_FLOAT).log(10)  # LOG(x) в DuckDB — десятичный
        base = self._const(n.this)
        if not isinstance(base, int | float):
            raise self._unsupported("LOG с основанием-выражением считает DuckDB")
        return self._tr(n.expression).cast(_FLOAT).log(float(base))

    def _null_safe(self, n: exp.NullSafeEQ | exp.NullSafeNEQ) -> pl.Expr:
        a, b = self._typed(n.this, n.expression), self._typed(n.expression, n.this)
        return a.eq_missing(b) if isinstance(n, exp.NullSafeEQ) else a.ne_missing(b)


def _compare(op: Callable[[pl.Expr, pl.Expr], pl.Expr]) -> Callable[[SqlTranslator, Any], pl.Expr]:
    def handler(s: SqlTranslator, n: Any) -> pl.Expr:
        return op(s._typed(n.this, n.expression), s._typed(n.expression, n.this))

    return handler


def _bin(op: Callable[[pl.Expr, pl.Expr], pl.Expr]) -> Callable[[SqlTranslator, Any], pl.Expr]:
    def handler(s: SqlTranslator, n: Any) -> pl.Expr:
        return op(s._tr(n.this), s._tr(n.expression))

    return handler


def _unary(fn: Callable[[pl.Expr], pl.Expr]) -> Callable[[SqlTranslator, Any], pl.Expr]:
    def handler(s: SqlTranslator, n: Any) -> pl.Expr:
        return fn(s._tr(n.this))

    return handler


def _date_part(part: str) -> Callable[[SqlTranslator, Any], pl.Expr]:
    fn = _DATE_PARTS[part]

    def handler(s: SqlTranslator, n: Any) -> pl.Expr:
        return fn(s._tr(n.this)).cast(_INT)

    return handler


def _current_date(s: SqlTranslator, n: Any) -> pl.Expr:
    return pl.lit(date.today())


_HANDLERS: dict[type, Callable[[SqlTranslator, Any], pl.Expr]] = {
    exp.Column: SqlTranslator._column,
    exp.Literal: SqlTranslator._literal,
    exp.Boolean: lambda s, n: pl.lit(bool(n.this)),
    exp.Null: lambda s, n: pl.lit(None),
    exp.Paren: lambda s, n: s._tr(n.this),
    exp.Neg: lambda s, n: -s._tr(n.this),
    exp.Not: lambda s, n: ~s._tr(n.this),
    exp.And: _bin(lambda a, b: a & b),
    exp.Or: _bin(lambda a, b: a | b),
    exp.Add: _bin(lambda a, b: a + b),
    exp.Sub: _bin(lambda a, b: a - b),
    exp.Mul: _bin(lambda a, b: a * b),
    exp.Mod: SqlTranslator._mod,
    exp.IntDiv: SqlTranslator._intdiv,
    exp.Div: SqlTranslator._div,
    exp.Pow: lambda s, n: s._tr(n.this).cast(_FLOAT).pow(s._tr(n.expression)),
    exp.EQ: _compare(lambda a, b: a == b),
    exp.NEQ: _compare(lambda a, b: a != b),
    exp.GT: _compare(lambda a, b: a > b),
    exp.GTE: _compare(lambda a, b: a >= b),
    exp.LT: _compare(lambda a, b: a < b),
    exp.LTE: _compare(lambda a, b: a <= b),
    exp.NullSafeEQ: SqlTranslator._null_safe,
    exp.NullSafeNEQ: SqlTranslator._null_safe,
    exp.DPipe: SqlTranslator._concat,
    exp.Concat: SqlTranslator._concat_fn,
    exp.Is: SqlTranslator._is,
    exp.In: SqlTranslator._in,
    exp.Between: SqlTranslator._between,
    exp.Like: SqlTranslator._like,
    exp.ILike: SqlTranslator._like,
    exp.Case: SqlTranslator._case,
    exp.If: SqlTranslator._if,
    exp.Coalesce: SqlTranslator._coalesce,
    exp.Nullif: SqlTranslator._nullif,
    exp.Greatest: SqlTranslator._greatest,
    exp.Least: SqlTranslator._least,
    exp.Round: SqlTranslator._round,
    exp.Cast: SqlTranslator._cast,
    exp.TryCast: SqlTranslator._cast,
    exp.Abs: _unary(lambda e: e.abs()),
    exp.Floor: _unary(lambda e: e.cast(_FLOAT).floor()),
    exp.Ceil: _unary(lambda e: e.cast(_FLOAT).ceil()),
    exp.Sqrt: _unary(lambda e: e.cast(_FLOAT).sqrt()),
    exp.Ln: _unary(lambda e: e.cast(_FLOAT).log()),
    exp.Exp: _unary(lambda e: e.cast(_FLOAT).exp()),
    exp.Sign: _unary(lambda e: e.sign()),
    exp.Log: SqlTranslator._log,
    exp.Lower: _unary(lambda e: e.str.to_lowercase()),
    exp.Upper: _unary(lambda e: e.str.to_uppercase()),
    exp.Length: _unary(lambda e: e.str.len_chars().cast(_INT)),
    exp.Trim: SqlTranslator._trim,
    exp.Substring: SqlTranslator._substring,
    exp.Left: SqlTranslator._left,
    exp.Right: SqlTranslator._right,
    exp.Replace: SqlTranslator._replace,
    exp.RegexpLike: SqlTranslator._regexp_like,
    exp.RegexpFullMatch: SqlTranslator._regexp_full,
    exp.RegexpReplace: SqlTranslator._regexp_replace,
    exp.RegexpExtract: SqlTranslator._regexp_extract,
    exp.SplitPart: SqlTranslator._split_part,
    exp.Contains: SqlTranslator._contains,
    exp.StartsWith: SqlTranslator._starts,
    exp.EndsWith: SqlTranslator._ends,
    exp.Pad: SqlTranslator._pad,
    exp.TimestampTrunc: SqlTranslator._trunc_date,
    exp.DateTrunc: SqlTranslator._trunc_date,
    exp.Extract: SqlTranslator._extract,
    exp.Year: _date_part("YEAR"),
    exp.Quarter: _date_part("QUARTER"),
    exp.Month: _date_part("MONTH"),
    exp.Day: _date_part("DAY"),
    exp.Week: _date_part("WEEK"),
    exp.DayOfWeek: _date_part("DOW"),
    exp.DayOfWeekIso: _date_part("ISODOW"),
    exp.DayOfYear: _date_part("DOY"),
    exp.Hour: _date_part("HOUR"),
    exp.Minute: _date_part("MINUTE"),
    exp.Second: _date_part("SECOND"),
    exp.TimeToStr: SqlTranslator._strftime,
    exp.StrToTime: SqlTranslator._strptime,
    exp.DateDiff: SqlTranslator._datediff,
    exp.DateFromParts: SqlTranslator._make_date,
    exp.LastDay: SqlTranslator._last_day,
    exp.CurrentDate: _current_date,
}
