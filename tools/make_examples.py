"""Генератор синтетических примеров для ``examples/``: выгрузки продаж за три месяца, план
на квартал и синтетический шаблон оформления.

Данные выдуманы, но устроены как настоящие выгрузки: Windows-1251 и «;», русские числа
(«12 345,67», неразрывные пробелы, отрицательные в скобках), даты «дд.мм.гггг» и с временем,
дубликаты заказов, возвраты, а у файлов разных месяцев переименованы и переставлены столбцы
и добавлен лишний столбец. Корпоративный шаблон пользователя в репозиторий не кладётся;
вместо него — синтетический шаблон 16:9 с теми же особенностями (``tools/make_template.py``).

Запуск из корня репозитория::

    uv run python tools/make_examples.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import xlsxwriter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_template import write_template

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
NBSP = chr(0xA0)

REGIONS = {
    "Москва": 2.4,
    "Санкт-Петербург": 1.6,
    "Казань": 0.9,
    "Екатеринбург": 1.0,
    "Новосибирск": 0.8,
}
MANAGERS = {
    "Москва": ["Иванова А.", "Петров С.", "Смирнова Е."],
    "Санкт-Петербург": ["Кузнецов Д.", "Попова М."],
    "Казань": ["Галиев Р."],
    "Екатеринбург": ["Соколов И.", "Лебедева О."],
    "Новосибирск": ["Морозов К."],
}
CHANNELS = ["Сайт", "Офис", "Партнёр"]
MONTHS = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
GROWTH = {1: 1.0, 2: 1.06, 3: 1.15}


def ru_amount(v: float, rnd: random.Random) -> str:
    """Сумма так, как её пишут русские выгрузки: пробел или неразрывный пробел в разрядах,
    запятая в дробной части, отрицательные — в скобках."""
    sep = NBSP if rnd.random() < 0.5 else " "
    text = f"{abs(v):,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", sep)
    return f"({text})" if v < 0 else text


def make_orders(month: date, rnd: random.Random, start_no: int) -> list[dict[str, object]]:
    days = ((month.replace(month=month.month + 1) if month.month < 12 else date(month.year + 1, 1, 1)) - month).days
    rows = []
    no = start_no
    for region, weight in REGIONS.items():
        n = int(110 * weight)
        for _ in range(n):
            no += 1
            d = datetime.combine(month + timedelta(days=rnd.randrange(days)), datetime.min.time())
            d += timedelta(hours=rnd.randrange(9, 20), minutes=rnd.randrange(60))
            amount = round(rnd.lognormvariate(10.4, 0.55) * GROWTH[month.month], 2)
            if rnd.random() < 0.03:
                amount = -round(amount * rnd.uniform(0.2, 1.0), 2)  # возврат
            rows.append(
                {
                    "date": d,
                    "order_no": f"ЗК-{no:06d}",
                    "region": region,
                    "manager": rnd.choice(MANAGERS[region]),
                    "channel": rnd.choice(CHANNELS),
                    "amount": amount,
                }
            )
    # Часть заказов выгружена дважды (дубликаты по номеру заказа).
    rows += [dict(r) for r in rnd.sample(rows, k=len(rows) // 50)]
    rows.sort(key=lambda r: r["date"])  # type: ignore[arg-type, return-value]
    return rows


def write_sales(folder: Path, rnd: random.Random) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    # Январь: исходные названия столбцов, даты без времени.
    # Февраль: столбцы переименованы («Дата», «№ заказа», «Сумма, руб.»), даты со временем.
    # Март: столбцы переставлены и добавлен лишний «Канал».
    layouts = {
        1: (
            ["Дата заказа", "Номер заказа", "Регион", "Менеджер", "Сумма"],
            ["date", "order_no", "region", "manager", "amount"],
            "%d.%m.%Y",
        ),
        2: (
            ["Дата", "№ заказа", "Регион", "Менеджер", "Сумма, руб."],
            ["date", "order_no", "region", "manager", "amount"],
            "%d.%m.%Y %H:%M",
        ),
        3: (
            ["Номер заказа", "Регион", "Дата заказа", "Канал", "Менеджер", "Сумма"],
            ["order_no", "region", "date", "channel", "manager", "amount"],
            "%d.%m.%Y",
        ),
    }
    start = 0
    for month in MONTHS:
        header, keys, date_fmt = layouts[month.month]
        rows = make_orders(month, rnd, start)
        start += 1000
        lines = [";".join(header)]
        for r in rows:
            values = []
            for k in keys:
                v = r[k]
                if k == "date":
                    values.append(v.strftime(date_fmt))  # type: ignore[attr-defined]
                elif k == "amount":
                    values.append(ru_amount(float(v), rnd))  # type: ignore[arg-type]
                else:
                    values.append(str(v))
            lines.append(";".join(values))
        path = folder / f"Продажи_{month:%Y-%m}.csv"
        path.write_bytes(("\r\n".join(lines) + "\r\n").encode("cp1251"))
        print(f"{path.relative_to(ROOT)}: {len(rows)} строк")


def write_plan(folder: Path, rnd: random.Random) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "План_2026-Q1.xlsx"
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("План")
    bold = wb.add_format({"bold": True})
    date_fmt = wb.add_format({"num_format": "dd.mm.yyyy"})
    money = wb.add_format({"num_format": "# ##0"})
    ws.write(0, 0, "План продаж на I квартал 2026", bold)
    ws.write_row(1, 0, ["Месяц", "Регион", "План, ₽"], bold)
    r = 2
    for month in MONTHS:
        for region, weight in REGIONS.items():
            # План ≈ ожидаемая выручка без НДС ± 10%.
            plan = round(110 * weight * 36_000 * GROWTH[month.month] / 1.2 * rnd.uniform(0.95, 1.1), -3)
            ws.write_datetime(r, 0, datetime.combine(month, datetime.min.time()), date_fmt)
            ws.write(r, 1, region)
            ws.write_number(r, 2, plan, money)
            r += 1
    ws.set_column(0, 0, 12)
    ws.set_column(1, 1, 18)
    ws.set_column(2, 2, 14)
    wb.close()
    print(f"{path.relative_to(ROOT)}: {r - 2} строк")


def main() -> None:
    rnd = random.Random(2026)
    write_sales(EXAMPLES / "sales" / "data" / "sales", rnd)
    write_plan(EXAMPLES / "sales" / "data" / "plan", rnd)
    out = EXAMPLES / "templates" / "synthetic.pptx"
    print(f"{out.relative_to(ROOT)}: {write_template(out)}")


if __name__ == "__main__":
    main()
