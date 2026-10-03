"""Импорт синтетического шаблона с особенностями корпоративного (tools/make_template.py)."""

from pathlib import Path

import pytest

from autogenerator.contracts import IssueLevel, LayoutRole, ThemeManifest
from autogenerator.theme import describe, import_template

SYNTHETIC = Path(__file__).resolve().parents[3] / "examples" / "templates" / "synthetic.pptx"


@pytest.fixture(scope="module")
def manifest(tmp_path_factory: pytest.TempPathFactory) -> ThemeManifest:
    return import_template(SYNTHETIC, tmp_path_factory.mktemp("theme"))


def test_layouts_of_both_masters(manifest: ThemeManifest):
    second = [lay for lay in manifest.layouts if lay.master == 1]
    assert [lay.name for lay in second] == ["Только заголовок", "Последний", "Заголовок и объект"]
    copy = second[0]
    assert not copy.preserve and copy.slides == 5
    # Для новых слайдов берётся такая же копия с preserve из первого мастера.
    title_only = manifest.role(LayoutRole.TITLE_ONLY)
    assert title_only is not None
    chosen = next(lay for lay in manifest.layouts if lay.key == title_only.layout_key and lay.master == 0)
    assert chosen.preserve
    assert manifest.role(LayoutRole.FINAL).layout_name == "Последний"
    # Макет без геометрии ни под какую роль не предлагается.
    assert "Заголовок и объект" not in {r.layout_name for r in manifest.roles}
    assert len({lay.fingerprint for lay in manifest.layouts}) == len(manifest.layouts)


def test_markers_split_into_runs(manifest: ThemeManifest):
    s2 = manifest.slides[1]
    assert s2.marker_names == ["Месяц", "Год", "Выручка", "Прирост+", "Доля"]
    growth = next(m for m in s2.markers if m.name == "Прирост+")
    assert growth.runs > 1 and growth.replaceable
    assert growth.text_after.startswith("%")
    assert growth.style.bold and growth.style.size
    month = next(m for m in s2.markers if m.name == "Месяц")
    assert month.style.lang == "en-US"  # метка начинается в прогоне с английским языком
    cover = manifest.slides[0]
    assert cover.marker_names == ["Месяц", "Год"]


def test_charts_and_table(manifest: ThemeManifest):
    s2, s3, s4, s5, s6 = manifest.slides[1:6]
    [combo] = s2.charts
    assert [(g.kind, g.grouping, len(g.series), g.secondary) for g in combo.groups] == [
        ("bar", "stacked", 2, False),
        ("line", "standard", 1, True),
    ]
    assert combo.categories == 3 and not combo.labels_per_category
    [pie] = s3.charts
    assert pie.groups[0].kind == "pie" and pie.categories == 6 and pie.point_settings >= 6
    [bars] = s4.charts
    assert bars.groups[0].direction == "bar" and bars.groups[0].grouping == "percentStacked"
    assert bars.labels_per_category
    lower, upper = s5.charts
    assert lower.overlaid == [upper.shape_id] and upper.overlaid == [lower.shape_id]
    [table] = s6.tables
    assert (table.rows, table.cols) == (5, 4)
    assert table.header == ["Регион", "Месяц 1", "Месяц 2", "Месяц 3"]
    assert s6.layout_name == "Заголовок и объект"


def test_lint_report(manifest: ThemeManifest):
    codes = {(i.code, i.slide) for i in manifest.lint}
    assert ("marker_unreplaceable", 7) in codes
    assert ("marker_in_object", 7) in codes
    assert ("hardcoded_year", 7) in codes
    assert ("slide_reference", 7) in codes
    assert ("duplicate_shape_name", 2) in codes and ("duplicate_shape_name", 7) in codes
    assert ("off_slide", 7) in codes
    assert ("duplicate_marker", 7) in codes
    assert ("layout_no_geometry", None) in codes
    same = [i for i in manifest.lint if i.code == "same_marker"]
    assert any("{{Доля}}" in i.message and "2, 3" in i.message for i in same)
    # Обложка с логотипом у края не считается фигурой за краем слайда.
    assert ("off_slide", 1) not in codes
    errors = {i.slide for i in manifest.lint if i.level == IssueLevel.ERROR}
    assert errors == {7}


def test_describe(manifest: ThemeManifest):
    text = describe(manifest, name="synthetic.pptx")
    assert "Слайды-образцы: меток" in text
    assert "категории закреплены надписями" in text
    assert "[ошибка] слайд 7" in text


def test_cover_logo_is_a_design_element(manifest: ThemeManifest):
    # Логотип обложки на макете «Title Slide» отсутствует, а такая же группа есть на макете
    # «Последний» с preserve: она и берётся, а положение — как на обложке.
    title = manifest.role(LayoutRole.TITLE)
    assert title is not None and len(title.decorations) == 1
    logo = title.decorations[0]
    final = manifest.role(LayoutRole.FINAL)
    assert final is not None and not final.decorations
    assert (logo.name, logo.kind, logo.from_layout, logo.from_slide) == ("Логотип", "group", final.layout_key, None)
    assert round(logo.geometry.y / 914400, 1) == 6.2
    # У ролей со слайдами содержания элементов оформления нет.
    assert not manifest.role(LayoutRole.TITLE_ONLY).decorations
