"""Генератор больших синтетических выгрузок для бенчмарков (ARCHITECTURE.md, разделы 7 и 13).

Выгрузка устроена как настоящая: 30 столбцов продаж, русские числа («12 345,67» с обычными
и неразрывными пробелами, отрицательные в скобках), даты «дд.мм.гггг», «да/нет», пустые
значения, кавычки и «;» внутри комментария. Строки генерируются выражениями Polars порциями,
поэтому 10 млн строк пишутся за десятки секунд и без большого расхода памяти.

Несколько загрузок (``--uploads``) идут месяц за месяцем, и часть номеров заказов
повторяется между соседними загрузками: так проверяется удаление дубликатов по всей истории.

Примеры::

    uv run python tools/make_big_data.py csv big/ --rows 10000000                 # 10 млн, cp1251, «;»
    uv run python tools/make_big_data.py csv big/ --rows 10000000 --uploads 5     # история 50 млн
    uv run python tools/make_big_data.py xlsx big/ --rows 1000000                 # лист Excel 1 млн
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import polars as pl

NBSP = chr(0xA0)
CHUNK_ROWS = 500_000
EXCEL_MAX_ROWS = 1_048_576

REGIONS = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Самара", "Краснодар", "Пермь"]
CHANNELS = ["Сайт", "Офис", "Партнёр", "Маркетплейс"]
CATEGORIES = [
    "Ноутбуки",
    "Мониторы",
    "Периферия",
    "Сети",
    "Хранение",
    "Печать",
    "Серверы",
    "Софт",
    "Услуги",
    "Кабели",
]
STATUSES = ["Оплачен", "Отгружен", "Доставлен", "Отменён", "В работе"]
PAYMENTS = ["Безналичный", "Карта", "Наличные", "Рассрочка"]
WAREHOUSES = ["Центральный", "Северный", "Южный", "Уральский", "Сибирский"]
COMMENTS = [
    None,
    None,
    None,
    None,
    None,
    None,
    "Срочно",
    "Позвонить клиенту; уточнить адрес",
    'Клиент "VIP"',
    "Повторный заказ",
]

# Названия столбцов выгрузки в порядке файла.
HEADER = [
    "Дата заказа",
    "Номер заказа",
    "Строка",
    "Регион",
    "Город",
    "Филиал",
    "Менеджер",
    "Клиент",
    "ИНН клиента",
    "Канал",
    "Категория",
    "Подкатегория",
    "Товар",
    "Артикул",
    "Количество",
    "Цена",
    "Сумма",
    "Скидка, %",
    "Сумма скидки",
    "НДС",
    "Сумма без НДС",
    "Себестоимость",
    "Валовая прибыль",
    "Статус",
    "Дата оплаты",
    "Дата отгрузки",
    "Способ оплаты",
    "Склад",
    "Возврат",
    "Комментарий",
]


def _pick(values: Sequence[str | None], idx: pl.Expr) -> pl.Expr:
    return idx.replace_strict(dict(enumerate(values)), return_dtype=pl.String)


def _money(cents: pl.Expr, sep: pl.Expr) -> pl.Expr:
    """Сумма в копейках → «1 234 567,89»; отрицательные — в скобках."""
    neg = cents < 0
    a = cents.abs()
    whole = a // 100
    frac = (a % 100).cast(pl.String).str.zfill(2)
    g1 = whole // 1_000_000
    g2 = (whole // 1000) % 1000
    g3 = whole % 1000
    s = (
        pl.when(g1 > 0)
        .then(
            pl.concat_str(
                [g1.cast(pl.String), sep, g2.cast(pl.String).str.zfill(3), sep, g3.cast(pl.String).str.zfill(3)]
            )
        )
        .when(g2 > 0)
        .then(pl.concat_str([g2.cast(pl.String), sep, g3.cast(pl.String).str.zfill(3)]))
        .otherwise(g3.cast(pl.String))
    )
    s = pl.concat_str([s, pl.lit(","), frac])
    return pl.when(neg).then(pl.concat_str([pl.lit("("), s, pl.lit(")")])).otherwise(s)


def _date(d: pl.Expr) -> pl.Expr:
    return d.dt.strftime("%d.%m.%Y")


def chunk(start: int, n: int, upload: int, month: date, total_rows: int) -> pl.DataFrame:
    """Строки ``start … start+n`` загрузки номер ``upload`` (с нуля) за месяц ``month``."""
    i = pl.int_range(start, start + n, dtype=pl.UInt64, eager=True).alias("i").to_frame()
    idx = pl.col("i")
    seed = 1000 * (upload + 1)

    def rnd(k: int, salt: int) -> pl.Expr:
        return (idx.hash(seed + salt) % k).cast(pl.Int64)

    next_month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    days = (next_month - month).days
    order_day = pl.lit(month) + pl.duration(days=rnd(days, 1))
    # Номер заказа: 5% строк повторяют заказы прошлой загрузки, 1% — заказы этой же.
    base = upload * total_rows
    prev_base = max(upload - 1, 0) * total_rows
    order = (
        pl.when((rnd(100, 2) < 5) & (upload > 0))
        .then(prev_base + rnd(total_rows, 3))
        .when(rnd(100, 4) < 1)
        .then(base + rnd(max(start + n, 1), 5))
        .otherwise(base + idx.cast(pl.Int64))
    )
    qty = rnd(20, 6) + 1
    price_cents = (rnd(500_000, 7) + 1_000) * 10
    discount = rnd(4, 8) * 5
    amount = qty * price_cents
    disc_cents = -(amount * discount // 100)
    ret = rnd(100, 9) < 2
    amount = pl.when(ret).then(-amount).otherwise(amount)
    net = amount * 100 // 120
    vat = amount - net
    cost = net * (60 + rnd(25, 10)) // 100
    sep = pl.when(rnd(2, 11) == 0).then(pl.lit(" ")).otherwise(pl.lit(NBSP))
    paid = pl.when(rnd(10, 12) < 8).then(_date(order_day + pl.duration(days=rnd(10, 13)))).otherwise(pl.lit(None))
    cols = [
        _date(order_day),
        pl.concat_str([pl.lit("ЗК-"), order.cast(pl.String).str.zfill(9)]),
        (rnd(5, 14) + 1).cast(pl.String),
        _pick(REGIONS, rnd(len(REGIONS), 15)),
        pl.concat_str([pl.lit("Город "), rnd(60, 16).cast(pl.String)]),
        pl.concat_str([pl.lit("Филиал "), rnd(25, 17).cast(pl.String)]),
        pl.concat_str([pl.lit("Менеджер "), rnd(300, 18).cast(pl.String)]),
        pl.concat_str([pl.lit("ООО Клиент "), rnd(50_000, 19).cast(pl.String)]),
        (rnd(9_000_000_000, 20) + 7_700_000_000).cast(pl.String),
        _pick(CHANNELS, rnd(len(CHANNELS), 21)),
        _pick(CATEGORIES, rnd(len(CATEGORIES), 22)),
        pl.concat_str([pl.lit("Подкатегория "), rnd(80, 23).cast(pl.String)]),
        pl.concat_str([pl.lit("Товар "), rnd(5_000, 24).cast(pl.String)]),
        pl.concat_str([pl.lit("A-"), rnd(99_999, 25).cast(pl.String).str.zfill(5)]),
        qty.cast(pl.String),
        _money(price_cents, sep),
        _money(amount, sep),
        discount.cast(pl.String),
        _money(disc_cents, sep),
        _money(vat, sep),
        _money(net, sep),
        _money(cost, sep),
        _money(net - cost, sep),
        _pick(STATUSES, rnd(len(STATUSES), 26)),
        paid,
        _date(order_day + pl.duration(days=rnd(5, 27))),
        _pick(PAYMENTS, rnd(len(PAYMENTS), 28)),
        _pick(WAREHOUSES, rnd(len(WAREHOUSES), 29)),
        pl.when(ret).then(pl.lit("да")).otherwise(pl.lit("нет")),
        _pick(COMMENTS, rnd(len(COMMENTS), 30)),
    ]
    return i.select([c.alias(h) for c, h in zip(cols, HEADER, strict=True)])


def month_of(upload: int, first: date) -> date:
    m = first.month - 1 + upload
    return date(first.year + m // 12, m % 12 + 1, 1)


def write_csv(path: Path, rows: int, upload: int, month: date, encoding: str, delimiter: str) -> None:
    with path.open("wb") as f:
        f.write((delimiter.join(HEADER) + "\r\n").encode(encoding))
        for start in range(0, rows, CHUNK_ROWS):
            df = chunk(start, min(CHUNK_ROWS, rows - start), upload, month, rows)
            text = df.write_csv(separator=delimiter, include_header=False, line_terminator="\r\n")
            f.write(text.encode(encoding))


def write_xlsx(path: Path, rows: int, upload: int, month: date) -> None:
    """Лист на 1 млн строк с настоящими типами ячеек (числа, даты); больше 1 048 575 строк —
    на следующих листах с той же шапкой, как делают учётные системы."""
    import xlsxwriter

    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True})
    date_fmt = wb.add_format({"num_format": "dd.mm.yyyy"})
    money_fmt = wb.add_format({"num_format": "# ##0,00"})
    money_cols = {"Цена", "Сумма", "Сумма скидки", "НДС", "Сумма без НДС", "Себестоимость", "Валовая прибыль"}
    int_cols = {"Строка", "Количество", "Скидка, %", "ИНН клиента"}
    date_cols = {"Дата заказа", "Дата оплаты", "Дата отгрузки"}
    per_sheet = EXCEL_MAX_ROWS - 1
    written = 0
    sheet_no = 0
    ws = None
    r = 0
    for start in range(0, rows, CHUNK_ROWS):
        df = chunk(start, min(CHUNK_ROWS, rows - start), upload, month, rows)
        typed = df.select(
            [
                pl.col(h).str.to_date("%d.%m.%Y", strict=False)
                if h in date_cols
                else (
                    pl.col(h)
                    .str.replace_all(f"[ {NBSP}]", "")
                    .str.replace(r"^\((.*)\)$", "-$1")
                    .str.replace(",", ".", literal=True)
                    .cast(pl.Float64)
                    if h in money_cols
                    else (pl.col(h).cast(pl.Int64) if h in int_cols else pl.col(h))
                )
                for h in HEADER
            ]
        )
        for row in typed.iter_rows():
            if ws is None or r > per_sheet:
                sheet_no += 1
                ws = wb.add_worksheet(f"Лист{sheet_no}")
                ws.write_row(0, 0, HEADER)
                r = 1
            for c, (h, v) in enumerate(zip(HEADER, row, strict=True)):
                if v is None:
                    continue
                if h in date_cols:
                    ws.write_datetime(r, c, datetime.combine(v, datetime.min.time()), date_fmt)
                elif h in money_cols:
                    ws.write_number(r, c, v, money_fmt)
                elif h in int_cols:
                    ws.write_number(r, c, v)
                else:
                    ws.write_string(r, c, v)
            r += 1
            written += 1
    wb.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make_big_data.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("format", choices=["csv", "xlsx"])
    ap.add_argument("out", type=Path, help="папка для файлов")
    ap.add_argument("--rows", type=int, default=10_000_000, help="строк в одной загрузке")
    ap.add_argument("--uploads", type=int, default=1, help="сколько загрузок (месяцев) подряд")
    ap.add_argument("--first-month", default="2026-01", help="месяц первой загрузки, ГГГГ-ММ")
    ap.add_argument("--encoding", default="cp1251", help="кодировка CSV: cp1251 или utf-8")
    ap.add_argument("--delimiter", default=";")
    a = ap.parse_args(argv)
    first = datetime.strptime(a.first_month, "%Y-%m").date()
    a.out.mkdir(parents=True, exist_ok=True)
    for up in range(a.uploads):
        month = month_of(up, first)
        path = a.out / f"Продажи_{month:%Y-%m}_{a.rows}.{a.format}"
        t0 = time.perf_counter()
        if a.format == "csv":
            write_csv(path, a.rows, up, month, a.encoding, a.delimiter)
        else:
            write_xlsx(path, a.rows, up, month)
        size = path.stat().st_size / 2**20
        print(f"{path}: {a.rows:,} строк, {size:,.0f} МБ, {time.perf_counter() - t0:.0f} с".replace(",", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
