"""
Create a fresh sprs.xlsx template matching the spec layout, in case the
user does not yet have one. Run once with:

    python create_template.py path/to/sprs.xlsx
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


def create_template(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Trang tính1"

    bold = Font(name="Arial", bold=True)
    normal = Font(name="Arial")
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", start_color="D9D9D9")
    center = Alignment(horizontal="center", vertical="center")

    # Summary block
    ws["A1"] = "TÓM TẮT"
    ws["A1"].font = bold

    ws["A2"] = "Tổng thời gian gọi (giờ):"
    ws["A2"].font = normal
    ws["B2"].number_format = "[h]:mm"

    ws["A3"] = "Thời gian bắt đầu:"
    ws["A3"].font = normal
    ws["B3"].number_format = "HH:mm"

    ws["A4"] = "Nhận xét chuyên cần:"
    ws["A4"].font = normal
    # Leave B4 empty — caller may write a default comment here.

    # Table headers in row 6
    headers = ["STT", "Bắt đầu", "Kết thúc", "Thời lượng"]
    for col_idx, txt in enumerate(headers, start=1):
        cell = ws.cell(row=6, column=col_idx, value=txt)
        cell.font = bold
        cell.alignment = center
        cell.fill = header_fill
        cell.border = border

    # 16 session rows (7..22)
    for i, row in enumerate(range(7, 23), start=1):
        ws.cell(row=row, column=1, value=i).font = normal
        ws.cell(row=row, column=1).alignment = center
        ws.cell(row=row, column=1).border = border

        ws.cell(row=row, column=2).number_format = "HH:mm"
        ws.cell(row=row, column=2).border = border
        ws.cell(row=row, column=2).alignment = center

        ws.cell(row=row, column=3).number_format = "HH:mm"
        ws.cell(row=row, column=3).border = border
        ws.cell(row=row, column=3).alignment = center

        ws.cell(row=row, column=4, value=0).number_format = "[h]:mm"
        ws.cell(row=row, column=4).border = border
        ws.cell(row=row, column=4).alignment = center

    # Column widths
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"Wrote template: {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python create_template.py path/to/sprs.xlsx", file=sys.stderr)
        sys.exit(1)
    create_template(Path(sys.argv[1]))
