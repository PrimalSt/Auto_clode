"""Генератор синтетических примеров для ``examples/``: выгрузки продаж за три месяца, план
на квартал и синтетический шаблон оформления.

Данные выдуманы, но устроены как настоящие выгрузки: Windows-1251 и «;», русские числа
(«12 345,67», неразрывные пробелы, отрицательные в скобках), даты «дд.мм.гггг» и с временем,
дубликаты заказов, возвраты, а у файлов разных месяцев переименованы и переставлены столбцы
и добавлен лишний столбец. Корпоративный шаблон пользователя в репозиторий не кладётся;
вместо него — синтетический шаблон 16:9.

Запуск из корня репозитория::

    uv run python tools/make_examples.py
"""

from __future__ import annotations

import copy
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import xlsxwriter
from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE
from pptx.presentation import Presentation as PresentationT
from pptx.util import Emu, Inches, Pt

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


# --- синтетический шаблон -----------------------------------------------------------------

WIDTH, HEIGHT = Inches(13.333), Inches(7.5)
ACCENTS = {
    "accent1": "1F4E79",
    "accent2": "F28C28",
    "accent3": "2E8B57",
    "accent4": "7F7F7F",
    "accent5": "5B9BD5",
    "accent6": "C00000",
}
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _scale_xfrm(root: etree._Element, fx: float) -> None:
    """Растянуть по горизонтали все фигуры макета: шаблон python-pptx рассчитан на 4:3."""
    for off in root.iter(f"{A}off"):
        off.set("x", str(int(int(off.get("x", "0")) * fx)))
    for ext in root.iter(f"{A}ext"):
        if ext.get("cx") is not None:
            ext.set("cx", str(int(int(ext.get("cx", "0")) * fx)))


def _theme_colors(prs: PresentationT) -> None:
    master_part = prs.slide_master.part
    theme_part = next(r.target_part for r in master_part.rels.values() if r.reltype.endswith("/theme"))
    root = etree.fromstring(theme_part.blob)
    for name, rgb in ACCENTS.items():
        el = root.find(f".//{A}clrScheme/{A}{name}")
        if el is not None:
            for child in list(el):
                el.remove(child)
            etree.SubElement(el, f"{A}srgbClr", val=rgb)
    root.find(f".//{A}clrScheme").set("name", "Autogenerator")
    theme_part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _master_band(prs: PresentationT) -> None:
    """Полоса акцентного цвета сверху и подпись в подвале на мастере: так видно, что отчёт
    собран в шаблоне, а не в пустой презентации."""
    tmp = prs.slides.add_slide(prs.slide_layouts[6])
    band = tmp.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, WIDTH, Inches(0.18))
    band.fill.solid()
    band.fill.fore_color.rgb = RGBColor.from_string(ACCENTS["accent1"])
    band.line.fill.background()
    band.name = "Полоса шаблона"
    note = tmp.shapes.add_textbox(Inches(0.5), Inches(7.05), Inches(6), Inches(0.35))
    note.name = "Подпись шаблона"
    note.text_frame.text = "Синтетический шаблон Autogenerator"
    note.text_frame.word_wrap = True
    note.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    run = note.text_frame.paragraphs[0].runs[0]
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x7F, 0x7F, 0x7F)
    tree = prs.slide_master.shapes._spTree
    for sh in (band, note):
        tree.append(copy.deepcopy(sh._element))
    sld = prs.slides._sldIdLst[-1]
    prs.part.drop_rel(sld.rId)
    prs.slides._sldIdLst.remove(sld)


def write_template(path: Path) -> None:
    prs = Presentation()
    fx = WIDTH / prs.slide_width
    prs.slide_width, prs.slide_height = Emu(WIDTH), Emu(HEIGHT)
    _scale_xfrm(prs.slide_master._element, fx)
    for layout in prs.slide_master.slide_layouts:
        _scale_xfrm(layout._element, fx)
    _theme_colors(prs)
    _master_band(prs)
    # Слайд шаблона с метками — будущий слайд-образец (этап M3). В отчёт M0 он не попадает.
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Образец: продажи за {{Месяц}} {{Год}}"
    box = s.shapes.add_textbox(Inches(0.6), Inches(2), Inches(8), Inches(1))
    box.text_frame.text = "Выручка {{Выручка}} млн ₽, {{Прирост+}} к прошлому месяцу"
    props = prs.core_properties
    props.title = "Синтетический шаблон Autogenerator"
    props.author = "Autogenerator"
    props.created = props.modified = datetime(2026, 9, 30)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(path))
    print(f"{path.relative_to(ROOT)}: {len(prs.slide_master.slide_layouts)} макетов, 1 слайд-образец")


def main() -> None:
    rnd = random.Random(2026)
    write_sales(EXAMPLES / "sales" / "data" / "sales", rnd)
    write_plan(EXAMPLES / "sales" / "data" / "plan", rnd)
    write_template(EXAMPLES / "templates" / "synthetic.pptx")


if __name__ == "__main__":
    main()
