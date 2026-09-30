"""Единый диалект формул: SQL-выражения DuckDB → выражения Polars (ARCHITECTURE.md, раздел 6.4).

Формула разбирается sqlglot и переводится по таблице поддерживаемых конструкций. Этап M0
знает арифметику, сравнения, AND/OR/NOT, IS NULL, IN, BETWEEN, LIKE, CASE, COALESCE и
несколько функций. Если конструкции нет в таблице, выдаётся понятная ошибка; на этапе M2
такие узлы будут выполняться в DuckDB с тем же результатом.

Деление на ноль даёт пустое значение (NULL), а не бесконечность: показатель «нет данных»
лучше, чем «∞ %».
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, datetime
from typing import Any

import polars as pl
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from autogenerator.contracts import AgenError, ErrorCode

_CAST_TYPES: dict[str, pl.DataType] = {
    "INT": pl.Int64(),
    "INTEGER": pl.Int64(),
    "BIGINT": pl.Int64(),
    "SMALLINT": pl.Int64(),
    "TINYINT": pl.Int64(),
    "DOUBLE": pl.Float64(),
    "FLOAT": pl.Float64(),
    "DECIMAL": pl.Float64(),
    "REAL": pl.Float64(),
    "VARCHAR": pl.String(),
    "TEXT": pl.String(),
    "CHAR": pl.String(),
    "DATE": pl.Date(),
    "TIMESTAMP": pl.Datetime("us"),
    "DATETIME": pl.Datetime("us"),
    "BOOLEAN": pl.Boolean(),
}


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


class SqlTranslator:
    """Переводчик формул. ``columns`` — допустимые имена столбцов (``None`` — не проверять)."""

    def __init__(self, columns: Iterable[str] | None = None) -> None:
        self.columns = set(columns) if columns is not None else None

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
            or isinstance(tree, exp.Select | exp.Union | exp.Insert | exp.Command)
            or not isinstance(tree, exp.Expression)
        ):
            raise AgenError(ErrorCode.EXPRESSION, f"«{text}» — не выражение, а запрос")
        return tree

    def columns_in(self, sql: str) -> set[str]:
        return {c.name for c in self.parse(sql).find_all(exp.Column)}

    def expr(self, sql: str) -> pl.Expr:
        self._sql = sql
        return self._tr(self.parse(sql))

    # --- перевод ---------------------------------------------------------------

    def _fail(self, what: str) -> AgenError:
        return AgenError(ErrorCode.EXPRESSION, f"В формуле «{self._sql}» {what}")

    def _tr(self, n: exp.Expression) -> pl.Expr:
        handler = _HANDLERS.get(type(n))
        if handler is not None:
            return handler(self, n)
        if isinstance(n, exp.Anonymous | exp.Func):
            name = n.sql_name() if not isinstance(n, exp.Anonymous) else str(n.this)
            raise self._fail(f"функция {name.upper()} пока не поддерживается")
        raise self._fail(f"конструкция «{n.sql(dialect='duckdb')}» пока не поддерживается")

    def _column(self, n: exp.Column) -> pl.Expr:
        name = n.name
        if self.columns is not None and name not in self.columns:
            known = ", ".join(sorted(c for c in self.columns if not c.startswith("_")))
            raise self._fail(f"нет столбца «{name}». Есть: {known}")
        return pl.col(name)

    def _literal(self, n: exp.Literal) -> pl.Expr:
        if n.is_string:
            return pl.lit(n.this)
        text = str(n.this)
        return pl.lit(int(text)) if re.fullmatch(r"-?\d+", text) else pl.lit(float(text))

    def _div(self, n: exp.Div) -> pl.Expr:
        a, b = self._tr(n.this), self._tr(n.expression)
        return pl.when(b != 0).then(a / b).otherwise(None)

    def _is(self, n: exp.Is) -> pl.Expr:
        if isinstance(n.expression, exp.Null):
            return self._tr(n.this).is_null()
        raise self._fail("поддерживается только IS NULL / IS NOT NULL")

    def _in(self, n: exp.In) -> pl.Expr:
        if n.args.get("query") is not None:
            raise self._fail("подзапросы в IN не поддерживаются")
        values = [self._const(v) for v in n.expressions]
        return self._tr(n.this).is_in(values)

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
        raise self._fail(f"в списке IN ожидались значения, а не «{n.sql()}»")

    def _between(self, n: exp.Between) -> pl.Expr:
        return self._tr(n.this).is_between(self._tr(n.args["low"]), self._tr(n.args["high"]))

    def _like(self, n: exp.Like | exp.ILike) -> pl.Expr:
        pattern = n.expression
        if not (isinstance(pattern, exp.Literal) and pattern.is_string):
            raise self._fail("шаблон LIKE должен быть строкой")
        rx = _like_to_regex(pattern.this)
        if isinstance(n, exp.ILike):
            rx = "(?i)" + rx
        return self._tr(n.this).str.contains(rx)

    def _case(self, n: exp.Case) -> pl.Expr:
        ifs = n.args.get("ifs") or []
        if not ifs:
            raise self._fail("CASE без WHEN")
        subject = n.this
        chain: Any = None
        for i in ifs:
            cond = self._tr(i.this) if subject is None else (self._tr(subject) == self._tr(i.this))
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
        return self._tr(n.this).round(int(self._const(d)) if d is not None else 0)

    def _cast(self, n: exp.Cast) -> pl.Expr:
        to = n.to.this.name if hasattr(n.to.this, "name") else str(n.to.this)
        target = _CAST_TYPES.get(str(to).upper())
        if target is None:
            raise self._fail(f"приведение к типу {to} пока не поддерживается")
        inner = n.this
        if isinstance(inner, exp.Literal) and inner.is_string:
            if isinstance(target, pl.Date):
                return pl.lit(date.fromisoformat(inner.this))
            if isinstance(target, pl.Datetime):
                return pl.lit(datetime.fromisoformat(inner.this))
        return self._tr(inner).cast(target, strict=False)

    def _concat(self, n: exp.DPipe) -> pl.Expr:
        return pl.concat_str([self._tr(n.this), self._tr(n.expression)])


def _bin(op: Callable[[pl.Expr, pl.Expr], pl.Expr]) -> Callable[[SqlTranslator, Any], pl.Expr]:
    def handler(s: SqlTranslator, n: Any) -> pl.Expr:
        return op(s._tr(n.this), s._tr(n.expression))

    return handler


def _short(e: ParseError) -> str:
    errs = getattr(e, "errors", None) or []
    if errs:
        first = errs[0]
        return f"{first.get('description', 'синтаксическая ошибка')} (позиция {first.get('col', '?')})"
    return str(e).splitlines()[0]


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
    exp.Mod: _bin(lambda a, b: a % b),
    exp.EQ: _bin(lambda a, b: a == b),
    exp.NEQ: _bin(lambda a, b: a != b),
    exp.GT: _bin(lambda a, b: a > b),
    exp.GTE: _bin(lambda a, b: a >= b),
    exp.LT: _bin(lambda a, b: a < b),
    exp.LTE: _bin(lambda a, b: a <= b),
    exp.DPipe: SqlTranslator._concat,
    exp.Div: SqlTranslator._div,
    exp.Is: SqlTranslator._is,
    exp.In: SqlTranslator._in,
    exp.Between: SqlTranslator._between,
    exp.Like: SqlTranslator._like,
    exp.ILike: SqlTranslator._like,
    exp.Case: SqlTranslator._case,
    exp.If: SqlTranslator._if,
    exp.Coalesce: SqlTranslator._coalesce,
    exp.Round: SqlTranslator._round,
    exp.Cast: SqlTranslator._cast,
    exp.Abs: lambda s, n: s._tr(n.this).abs(),
    exp.Lower: lambda s, n: s._tr(n.this).str.to_lowercase(),
    exp.Upper: lambda s, n: s._tr(n.this).str.to_uppercase(),
    exp.Length: lambda s, n: s._tr(n.this).str.len_chars(),
    exp.Trim: lambda s, n: s._tr(n.this).str.strip_chars(),
}
