"""Строит tracker/channel_tracker.xlsx: Дашборд, Метрики, Закупы, Продажи_рекламы.

Запуск: python3 build_tracker.py
После генерации файл обязательно прогоняется через recalc.py (пересчёт формул
в LibreOffice) — см. инструкцию в конце этого файла / README проекта.
"""
from __future__ import annotations

from datetime import date, timedelta

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT_NAME = "Arial"

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF", size=11)
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14, color="1F2A44")
SUBTITLE_FONT = Font(name=FONT_NAME, italic=True, size=10, color="666666")
LABEL_FONT = Font(name=FONT_NAME, bold=True, size=11)
INPUT_FONT = Font(name=FONT_NAME, color="0000FF", size=11)  # синий — заполняет человек
FORMULA_FONT = Font(name=FONT_NAME, color="000000", size=11)  # чёрный — формула, не трогать
ASSUMPTION_FILL = PatternFill("solid", fgColor="FFFF00")  # жёлтый — ключевое допущение
EXAMPLE_FILL = PatternFill("solid", fgColor="EAF3FF")

THIN = Side(style="thin", color="CCCCCC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

CURRENCY_FMT = "#,##0.00"
INT_FMT = "#,##0"
PCT_FMT = "0.0%"
DATE_FMT = "DD.MM.YYYY"

# Фиксированные номера строк на листе "Дашборд" — заданы явно константами, а не
# счётчиком по ходу генерации, потому что на них ссылаются формулы с ДРУГИХ
# листов (Закупы), которые строятся раньше, чем сам Дашборд. Если меняете
# порядок строк в _build_dashboard — обновите и эти константы.
DASH_SUBS_NOW_ROW = 4
DASH_SUBS_START_ROW = 5
DASH_GROWTH_ROW = 6
DASH_POSTS_ROW = 7
DASH_ADS_COUNT_ROW = 8
DASH_REVENUE_ROW = 9
DASH_SPEND_ROW = 10
DASH_BALANCE_ROW = 11
DASH_AVG_SUB_PRICE_ROW = 12
DASH_MONTHS_ROW = 13
DASH_ARPU_ROW = 14


def style_header_row(ws, row: int, n_cols: int) -> None:
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER


def autosize(ws, widths: dict[str, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[col].width = width


def add_legend(ws, row: int, col: int = 1) -> None:
    ws.cell(row=row, column=col, value="Легенда:").font = LABEL_FONT
    ws.cell(row=row + 1, column=col, value="Синий текст — заполняете вы").font = INPUT_FONT
    ws.cell(row=row + 2, column=col, value="Чёрный текст — формула, не редактировать").font = FORMULA_FONT
    ex = ws.cell(row=row + 3, column=col, value="Голубая заливка строки — пример для формата")
    ex.fill = EXAMPLE_FILL


def build() -> Workbook:
    wb = Workbook()

    ws_dash = wb.active
    ws_dash.title = "Дашборд"
    ws_metrics = wb.create_sheet("Метрики")
    ws_buys = wb.create_sheet("Закупы")
    ws_sales = wb.create_sheet("Продажи_рекламы")

    _build_metrics(ws_metrics)
    _build_buys(ws_buys)
    _build_sales(ws_sales)
    _build_dashboard(ws_dash)

    wb.active = 0
    return wb


def _build_metrics(ws) -> None:
    ws["A1"] = "Метрики канала"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Заполняйте раз в день (вручную или через digest_bot: python cli.py collect-metrics)"
    ws["A2"].font = SUBTITLE_FONT

    headers = ["Дата", "Подписчики", "Прирост за день", "Постов опубликовано", "Ср. охват поста (24ч)", "ERR, %", "Заметки"]
    row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    style_header_row(ws, row, len(headers))

    today = date.today()
    example_row = row + 1
    example_values = [today - timedelta(days=1), 1000, None, 1, 320, None, "пример — можно удалить"]
    for i, v in enumerate(example_values, start=1):
        c = ws.cell(row=example_row, column=i, value=v)
        c.fill = EXAMPLE_FILL
        c.font = INPUT_FONT if i in (1, 2, 4, 7) else FORMULA_FONT
    # Это первая строка данных — сравнивать прирост не с чем (строка выше — шапка
    # таблицы, а не число), поэтому формулы прироста тут нет, в отличие от всех
    # последующих строк.
    ws.cell(row=example_row, column=6, value=f"=IFERROR(E{example_row}/B{example_row},0)").font = FORMULA_FONT
    ws.cell(row=example_row, column=6).number_format = PCT_FMT

    second_row = example_row + 1
    second_values = [today, 1012, None, 2, 340, None, ""]
    for i, v in enumerate(second_values, start=1):
        c = ws.cell(row=second_row, column=i, value=v)
        c.fill = EXAMPLE_FILL
        c.font = INPUT_FONT if i in (1, 2, 4, 7) else FORMULA_FONT
    ws.cell(row=second_row, column=3, value=f"=B{second_row}-B{second_row - 1}").font = FORMULA_FONT
    ws.cell(row=second_row, column=6, value=f"=IFERROR(E{second_row}/B{second_row},0)").font = FORMULA_FONT
    ws.cell(row=second_row, column=6).number_format = PCT_FMT

    # заготовка формул ещё на 200 строк вперёд — просто вписывайте даты/цифры
    last_data_row = second_row
    for r in range(second_row + 1, second_row + 201):
        # IF(B{r}="",...) отдельно от IFERROR: 0-0 не считается ошибкой в Excel,
        # поэтому без явной проверки на пустую ячейку все 200 незаполненных
        # строк-заготовок показывали бы "0" вместо пустоты.
        ws.cell(row=r, column=3, value=f"=IF(B{r}=\"\",\"\",IFERROR(B{r}-B{r - 1},\"\"))").font = FORMULA_FONT
        ws.cell(row=r, column=6, value=f"=IFERROR(E{r}/B{r},\"\")").font = FORMULA_FONT
        ws.cell(row=r, column=6).number_format = PCT_FMT
        last_data_row = r

    for r in range(example_row, last_data_row + 1):
        ws.cell(row=r, column=1).number_format = DATE_FMT
        ws.cell(row=r, column=2).number_format = INT_FMT
        ws.cell(row=r, column=3).number_format = INT_FMT
        ws.cell(row=r, column=5).number_format = INT_FMT

    ws.freeze_panes = "A5"
    autosize(ws, {"A": 12, "B": 13, "C": 15, "D": 18, "E": 20, "F": 10, "G": 30})

    ws.cell(row=last_data_row + 2, column=1, value=(
        "Допущение: ERR = охват поста / подписчики на дату. Считается по последнему посту дня, "
        "а не по среднему за все посты — если постов несколько, посчитайте среднее сами в колонке E."
    )).font = SUBTITLE_FONT


def _build_buys(ws) -> None:
    ws["A1"] = "Закупы (продвижение канала)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Реклама, которую покупаете вы — посевы, Telegram Ads, взаимопиар"
    ws["A2"].font = SUBTITLE_FONT

    headers = [
        "Дата", "Площадка / канал", "Формат", "Стоимость", "Валюта",
        "Новых подписчиков", "Цена подписчика", "Окупаемость, мес.", "Комментарий",
    ]
    row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    style_header_row(ws, row, len(headers))

    example_row = row + 1
    example = [date.today() - timedelta(days=3), "@example_channel", "Посев", 25000, "RUB", 180, None, None, "пример — можно удалить"]
    for i, v in enumerate(example, start=1):
        c = ws.cell(row=example_row, column=i, value=v)
        c.fill = EXAMPLE_FILL
        c.font = INPUT_FONT if i in (1, 2, 3, 4, 5, 6, 9) else FORMULA_FONT
    ws.cell(row=example_row, column=7, value=f"=IFERROR(D{example_row}/F{example_row},0)").font = FORMULA_FONT
    ws.cell(
        row=example_row, column=8,
        value=f"=IFERROR(G{example_row}/Дашборд!$B${DASH_ARPU_ROW},0)",
    ).font = FORMULA_FONT

    last_data_row = example_row
    for r in range(example_row + 1, example_row + 200):
        ws.cell(row=r, column=7, value=f"=IFERROR(D{r}/F{r},\"\")").font = FORMULA_FONT
        ws.cell(
            row=r, column=8,
            value=f"=IFERROR(G{r}/Дашборд!$B${DASH_ARPU_ROW},\"\")",
        ).font = FORMULA_FONT
        last_data_row = r

    for r in range(example_row, last_data_row + 1):
        ws.cell(row=r, column=1).number_format = DATE_FMT
        ws.cell(row=r, column=4).number_format = CURRENCY_FMT
        ws.cell(row=r, column=6).number_format = INT_FMT
        ws.cell(row=r, column=7).number_format = CURRENCY_FMT
        ws.cell(row=r, column=8).number_format = "0.0"

    ws.freeze_panes = "A5"
    autosize(ws, {"A": 12, "B": 22, "C": 14, "D": 12, "E": 9, "F": 16, "G": 15, "H": 16, "I": 30})

    ws.cell(row=last_data_row + 2, column=1, value=(
        f"Окупаемость = цена подписчика / ARPU в месяц (лист Дашборд, ячейка B{DASH_ARPU_ROW}). "
        "Пока в Дашборд нет данных о доходе, колонка будет пустой/0 — это ожидаемо на старте."
    )).font = SUBTITLE_FONT


def _build_sales(ws) -> None:
    ws["A1"] = "Продажи рекламы (в своём канале)"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = "Статус: Забронировано / Опубликовано / Снято / Оплачено / Отменено"
    ws["A2"].font = SUBTITLE_FONT

    headers = [
        "Дата размещения", "Рекламодатель", "Контакт", "Формат", "Цена", "Валюта",
        "Просмотры факт", "CPM факт", "ERID", "Статус", "Комментарий",
    ]
    row = 4
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    style_header_row(ws, row, len(headers))

    example_row = row + 1
    example = [
        date.today() + timedelta(days=2), "Acme Software", "@acme_manager", "Пост 24ч", 150, "USD",
        None, None, "2Vtzqv...", "Забронировано", "пример — можно удалить",
    ]
    for i, v in enumerate(example, start=1):
        c = ws.cell(row=example_row, column=i, value=v)
        c.fill = EXAMPLE_FILL
        c.font = INPUT_FONT if i in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11) else FORMULA_FONT
    ws.cell(row=example_row, column=8, value=f"=IFERROR(E{example_row}/G{example_row}*1000,\"\")").font = FORMULA_FONT

    last_data_row = example_row
    for r in range(example_row + 1, example_row + 200):
        ws.cell(row=r, column=8, value=f"=IFERROR(E{r}/G{r}*1000,\"\")").font = FORMULA_FONT
        last_data_row = r

    for r in range(example_row, last_data_row + 1):
        ws.cell(row=r, column=1).number_format = DATE_FMT
        ws.cell(row=r, column=5).number_format = CURRENCY_FMT
        ws.cell(row=r, column=7).number_format = INT_FMT
        ws.cell(row=r, column=8).number_format = CURRENCY_FMT

    dv_col = 10
    from openpyxl.worksheet.datavalidation import DataValidation

    dv = DataValidation(
        type="list",
        formula1='"Забронировано,Опубликовано,Снято,Оплачено,Отменено"',
        allow_blank=True,
    )
    ws.add_data_validation(dv)
    dv.add(f"{get_column_letter(dv_col)}{example_row}:{get_column_letter(dv_col)}{last_data_row}")

    ws.freeze_panes = "A5"
    autosize(ws, {"A": 15, "B": 20, "C": 16, "D": 14, "E": 10, "F": 9, "G": 14, "H": 12, "I": 16, "J": 16, "K": 30})


def _build_dashboard(ws) -> None:
    ws["A1"] = "Дашборд канала"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        f"Все цифры здесь — формулы, которые тянут данные с других листов. "
        f"Ничего не вводите вручную, кроме B{DASH_MONTHS_ROW}."
    )
    ws["A2"].font = SUBTITLE_FONT

    # Строки жёстко привязаны к константам DASH_*_ROW в начале файла — так на
    # них можно безопасно ссылаться из формул на листах Закупы/Метрики,
    # которые строятся раньше этого листа.
    rows = [
        (DASH_SUBS_NOW_ROW, "Текущие подписчики",
         "=IFERROR(INDEX(Метрики!B5:B1000,COUNT(Метрики!B5:B1000)),0)", INT_FMT),
        (DASH_SUBS_START_ROW, "Подписчики на старте отслеживания",
         "=IFERROR(INDEX(Метрики!B5:B1000,1),0)", INT_FMT),
        (DASH_GROWTH_ROW, "Прирост подписчиков за всё время",
         f"=B{DASH_SUBS_NOW_ROW}-B{DASH_SUBS_START_ROW}", INT_FMT),
        (DASH_POSTS_ROW, "Постов опубликовано всего",
         "=SUM(Метрики!D5:D1000)", INT_FMT),
        (DASH_ADS_COUNT_ROW, "Рекламных размещений всего",
         "=COUNTA(Продажи_рекламы!B5:B1000)", INT_FMT),
        (DASH_REVENUE_ROW, "Выручка с рекламы (кроме отменённых)",
         '=SUMIFS(Продажи_рекламы!E5:E1000,Продажи_рекламы!J5:J1000,"<>Отменено")', CURRENCY_FMT),
        (DASH_SPEND_ROW, "Потрачено на закупы всего",
         "=SUM(Закупы!D5:D1000)", CURRENCY_FMT),
        (DASH_BALANCE_ROW, "Баланс: реклама − закупы",
         f"=B{DASH_REVENUE_ROW}-B{DASH_SPEND_ROW}", CURRENCY_FMT),
        (DASH_AVG_SUB_PRICE_ROW, "Средняя цена подписчика (закупы)",
         f"=IFERROR(B{DASH_SPEND_ROW}/SUM(Закупы!F5:F1000),0)", CURRENCY_FMT),
    ]

    for r, label, formula, fmt in rows:
        ws.cell(row=r, column=1, value=label).font = LABEL_FONT
        c = ws.cell(row=r, column=2, value=formula)
        c.font = FORMULA_FONT
        c.number_format = fmt
        c.border = BORDER

    # Единственная ручная ячейка на этом листе: сколько месяцев уже ведёте канал.
    ws.cell(row=DASH_MONTHS_ROW, column=1, value="Сколько месяцев отслеживаете (для ARPU)").font = LABEL_FONT
    months_cell = ws.cell(row=DASH_MONTHS_ROW, column=2, value=1)
    months_cell.font = INPUT_FONT
    months_cell.fill = ASSUMPTION_FILL
    months_cell.number_format = "0"
    months_cell.comment = Comment(
        "Впишите вручную количество месяцев, за которое накоплены данные "
        "(например, если ведёте канал 2.5 месяца — впишите 2.5). "
        "Используется только для расчёта ARPU/мес ниже.",
        "digest_bot tracker",
    )

    ws.cell(row=DASH_ARPU_ROW, column=1, value="ARPU с подписчика в месяц (реклама)").font = LABEL_FONT
    c = ws.cell(
        row=DASH_ARPU_ROW, column=2,
        value=f"=IFERROR(B{DASH_REVENUE_ROW}/B{DASH_SUBS_NOW_ROW}/B{DASH_MONTHS_ROW},0)",
    )
    c.font = FORMULA_FONT
    c.number_format = CURRENCY_FMT
    c.border = BORDER
    c.comment = Comment(
        "Грубая оценка: выручка с рекламы / текущие подписчики / число месяцев. "
        "Не учитывает рост базы подписчиков во времени — чем быстрее рос канал, "
        "тем сильнее эта формула ЗАНИЖАЕТ реальный ARPU. Используйте как ориентир, "
        "не как точную метрику для отчёта инвестору.",
        "digest_bot tracker",
    )

    notes_start = DASH_ARPU_ROW + 2
    ws.cell(row=notes_start, column=1, value="Как это использовать:").font = LABEL_FONT
    notes = [
        "1. Заполняйте лист Метрики раз в день (дата + подписчики + посты).",
        "2. Заполняйте лист Закупы каждый раз, когда покупаете рекламу для роста канала.",
        "3. Заполняйте лист Продажи_рекламы каждый раз, когда бронируете рекламу в своём канале.",
        "4. Этот лист посчитает всё сам — открывайте раз в неделю перед отчётом.",
    ]
    for i, note in enumerate(notes):
        ws.cell(row=notes_start + 1 + i, column=1, value=note).font = Font(name=FONT_NAME, size=10)

    add_legend(ws, row=notes_start + 1 + len(notes) + 2)

    autosize(ws, {"A": 40, "B": 18})
    ws.column_dimensions["B"].width = 20


if __name__ == "__main__":
    workbook = build()
    out_path = "channel_tracker.xlsx"
    workbook.save(out_path)
    print(f"Сохранено: {out_path}")
