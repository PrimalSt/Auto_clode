"""Шаг ``join``: объединение с другим входом сценария (F-208) — продажи и справочник
менеджеров, продажи и план.

Типы ключей выравниваются (дата и дата со временем — до даты, целые и дробные — до
дробных). При запуске шаг предупреждает о повторах ключа во втором входе (строки
размножатся) и о строках без пары. На больших данных объединение считает DuckDB.
"""

from __future__ import annotations

from typing import Any, Literal

import polars as pl
from pydantic import Field, model_validator

from autogenerator.contracts import (
    AgenError,
    ColumnTypes,
    DType,
    ErrorCode,
    ExpressionTools,
    SchemaTools,
    StepContext,
    StepPlugin,
)

from .steps import _Params, q

HOW_TEXT = {"left": "левое", "inner": "внутреннее", "full": "полное", "semi": "есть пара", "anti": "нет пары"}


class JoinParams(_Params):
    with_: str = Field(alias="with", description="id другого входа сценария")
    on: list[str] | dict[str, str] = Field(description="Ключи: список одинаковых имён или «столбец здесь: столбец там»")
    how: Literal["left", "inner", "full", "semi", "anti"] = Field(
        "left",
        description="left — все строки этого входа; inner — только с парой; full — все строки обоих; "
        "semi — только строки с парой, без столбцов второго входа; anti — только строки без пары",
    )
    columns: list[str] | None = Field(
        None, description="Какие столбцы второго входа добавить; пусто — все, кроме ключей"
    )
    suffix: str | None = Field(None, description="Окончание для совпавших имён столбцов; по умолчанию _<id входа>")

    @model_validator(mode="after")
    def _check(self) -> JoinParams:
        if not self.on:
            raise ValueError("нужны ключи объединения (on)")
        if self.how in ("semi", "anti") and self.columns:
            raise ValueError(f"при объединении {self.how} столбцы второго входа не добавляются")
        return self

    @property
    def left_keys(self) -> list[str]:
        return list(self.on) if isinstance(self.on, list) else list(self.on.keys())

    @property
    def right_keys(self) -> list[str]:
        return list(self.on) if isinstance(self.on, list) else list(self.on.values())

    @property
    def tail(self) -> str:
        return self.suffix if self.suffix is not None else f"_{self.with_}"


def _added(params: JoinParams, left: list[str], right: list[str]) -> list[tuple[str, str]]:
    """Столбцы второго входа, которые добавляются: (имя там, имя в результате)."""
    if params.how in ("semi", "anti"):
        return []
    cols = params.columns or [c for c in right if c not in params.right_keys and not c.startswith("_")]
    taken = set(left)
    out = []
    for c in cols:
        name = c if c not in taken else f"{c}{params.tail}"
        taken.add(name)
        out.append((c, name))
    return out


def _align(lt: Any, rt: Any, lk: str, rk: str) -> Any:
    """Общий тип ключей двух сторон или ошибка."""
    if lt == rt:
        return None
    if (lt == pl.Date or isinstance(lt, pl.Datetime)) and (rt == pl.Date or isinstance(rt, pl.Datetime)):
        return pl.Date()
    if lt.is_numeric() and rt.is_numeric():
        return pl.Float64() if (lt.is_float() or rt.is_float()) else pl.Int64()
    raise AgenError(
        ErrorCode.EXPRESSION,
        f"ключи разных типов: «{lk}» — {lt}, «{rk}» — {rt}",
        hint="Приведите их к одному типу шагом cast.",
    )


class JoinStep(StepPlugin):
    name = "join"
    title = "Объединить с входом"
    Params = JoinParams
    row_local = True
    """Строка результата зависит только от своей строки и второго входа: нижняя граница
    истории через объединение проталкивается (второй вход читается целиком)."""

    def inputs_used(self, params: Any, tools: ExpressionTools) -> list[str]:
        return [params.with_]

    def columns_used(self, params: Any, tools: ExpressionTools) -> set[str]:
        return set(params.left_keys)

    def key_columns(self, params: Any) -> dict[str, list[str]]:
        return {"data": params.left_keys, params.with_: params.right_keys}

    def output_schema(self, params: Any, schema: ColumnTypes, tools: SchemaTools) -> ColumnTypes | None:
        right = tools.input_schema(params.with_)
        if right is None:
            return None
        for k in params.right_keys:
            if k not in right:
                known = ", ".join(c for c in right if not c.startswith("_"))
                raise AgenError(ErrorCode.EXPRESSION, f"во входе «{params.with_}» нет ключа «{k}» (есть: {known})")
        for c in params.columns or []:
            if c not in right:
                raise AgenError(ErrorCode.EXPRESSION, f"во входе «{params.with_}» нет столбца «{c}»")
        out: ColumnTypes = dict(schema)
        for lk, rk in zip(params.left_keys, params.right_keys, strict=True):
            lt, rt = out.get(lk), right.get(rk)
            if lt is not None and rt is not None and lt != rt:
                if {lt, rt} == {DType.DATE, DType.DATETIME}:
                    out[lk] = DType.DATE
                elif {lt, rt} <= {DType.INT, DType.FLOAT}:
                    out[lk] = DType.FLOAT
                else:
                    raise AgenError(
                        ErrorCode.EXPRESSION,
                        f"ключи разных типов: «{lk}» — {lt}, «{rk}» во входе «{params.with_}» — {rt}",
                        hint="Приведите их к одному типу шагом cast.",
                    )
        for src, name in _added(params, list(schema), list(right)):
            out[name] = right[src]
        return out

    def apply(self, lf: pl.LazyFrame, params: Any, ctx: StepContext) -> pl.LazyFrame:
        right = ctx.input(params.with_)
        ls, rs = lf.collect_schema(), right.collect_schema()
        for k in params.left_keys:
            if k not in ls:
                raise AgenError(ErrorCode.EXPRESSION, f"нет ключа «{k}»")
        for k in params.right_keys:
            if k not in rs:
                raise AgenError(ErrorCode.EXPRESSION, f"во входе «{params.with_}» нет ключа «{k}»")
        lcast, rcast = [], []
        for lk, rk in zip(params.left_keys, params.right_keys, strict=True):
            common = _align(ls[lk], rs[rk], lk, rk)
            if common is not None:
                lcast.append(pl.col(lk).cast(common))
                rcast.append(pl.col(rk).cast(common))
        if lcast:
            lf = lf.with_columns(lcast)
            right = right.with_columns(rcast)
        added = _added(params, ls.names(), rs.names())
        right = right.select([*(pl.col(k) for k in params.right_keys), *(pl.col(s).alias(n) for s, n in added)])
        if not ctx.preview:
            self.warn_pairs(lf, right, params, ctx)
        if ctx.large:
            return self.duckdb(lf, right, params, added, ls.names(), ctx)
        return lf.join(
            right,
            left_on=params.left_keys,
            right_on=params.right_keys,
            how=params.how,
            coalesce=True,
            maintain_order="left",
        )

    def warn_pairs(self, lf: pl.LazyFrame, right: pl.LazyFrame, params: Any, ctx: StepContext) -> None:
        rkeys = right.select(params.right_keys)
        dup, lonely = pl.collect_all(
            [
                rkeys.group_by(params.right_keys).len().filter(pl.col("len") > 1).select(pl.len()),
                lf.join(
                    rkeys.unique(),
                    left_on=params.left_keys,
                    right_on=params.right_keys,
                    how="anti",
                ).select(pl.len()),
            ]
        )
        if dup.item() and params.how in ("left", "inner", "full"):
            ctx.warn(
                f"во входе «{params.with_}» у {dup.item()} ключей несколько строк: строки при объединении размножатся"
            )
        n = lonely.item()
        if n and params.how in ("left", "inner", "full"):
            tail = "они отброшены" if params.how == "inner" else "столбцы второго входа у них пустые"
            ctx.warn(f"{n} строк без пары во входе «{params.with_}»: {tail}")

    def duckdb(
        self,
        lf: pl.LazyFrame,
        right: pl.LazyFrame,
        params: Any,
        added: list[tuple[str, str]],
        left_cols: list[str],
        ctx: StepContext,
    ) -> pl.LazyFrame:
        on = " AND ".join(f"l.{q(lk)} = r.{q(rk)}" for lk, rk in zip(params.left_keys, params.right_keys, strict=True))
        if params.how in ("semi", "anti"):
            query = f'SELECT l.* FROM data AS l {params.how.upper()} JOIN "__right" AS r ON {on}'
            return ctx.sql(query, {"data": lf, "__right": right})
        keys = dict(zip(params.left_keys, params.right_keys, strict=True))
        cols = [
            f"COALESCE(l.{q(c)}, r.{q(keys[c])}) AS {q(c)}" if params.how == "full" and c in keys else f"l.{q(c)}"
            for c in left_cols
        ]
        cols += [f"r.{q(n)}" for _, n in added]
        how = {"left": "LEFT", "inner": "INNER", "full": "FULL OUTER"}[params.how]
        query = f'SELECT {", ".join(cols)} FROM data AS l {how} JOIN "__right" AS r ON {on}'
        return ctx.sql(query, {"data": lf, "__right": right})
