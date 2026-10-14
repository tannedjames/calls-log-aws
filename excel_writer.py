"""
Write sessions into the user-supplied Excel template (sprs.xlsx).

Behavior:
- Open the template, find the target sheet (default "Trang tính1").
- Fill B7:D22 with one row per session. Use Excel time formulas where helpful.
- Compute total span (B2) and first session start (B3) as Excel FORMULAS, so
  the workbook stays alive if the user edits sessions later.
- Preserve B4 (the "Nhận xét chuyên cần" comment).
- If more than 16 sessions exist, optionally insert extra rows below row 22,
  copying row 22's formatting.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from config import CONFIG
from session_builder import Session

log = logging.getLogger(__name__)


def _time_to_excel_fraction(time_str: str) -> float:
    """Convert HH:MM into Excel's fractional-day representation."""
    h, m = time_str.split(":")
    return (int(h) * 3600 + int(m) * 60) / 86400.0


def write_sessions(
    template_path: Path,
    output_path: Path,
    sessions: List[Session],
) -> dict:
    """
    Fill the template and save to output_path. Returns a stats dict.
    """
    wb = load_workbook(template_path)
    sheet_name = CONFIG.sheet_name
    if sheet_name not in wb.sheetnames:
        # Fall back to first sheet
        log.warning(
            "Sheet '%s' not found in template; using first sheet '%s'",
            sheet_name, wb.sheetnames[0],
        )
        sheet_name = wb.sheetnames[0]
    ws = wb[sheet_name]

    first_row = CONFIG.first_session_row
    last_row = CONFIG.last_session_row
    template_capacity = last_row - first_row + 1
    extra_needed = max(0, len(sessions) - template_capacity)

    # Expand the template if needed
    if extra_needed > 0:
        if not CONFIG.allow_extra_rows:
            raise RuntimeError(
                f"Template only supports {template_capacity} sessions but "
                f"{len(sessions)} were produced. Enable allow_extra_rows or "
                f"trim the input."
            )
        _expand_template(ws, last_row, extra_needed)
        last_row += extra_needed
        log.info("Inserted %d extra session rows", extra_needed)

    # Clear out previous session-area B/C, set D default 0:00 -- without
    # touching column A (numbering) or column widths/borders.
    for row in range(first_row, last_row + 1):
        ws[f"{CONFIG.col_start}{row}"] = None
        ws[f"{CONFIG.col_end}{row}"] = None
        ws[f"{CONFIG.col_span}{row}"] = 0  # numeric 0; format gives "0:00"
        ws[f"{CONFIG.col_span}{row}"].number_format = CONFIG.duration_format
        ws[f"{CONFIG.col_start}{row}"].number_format = "HH:mm"
        ws[f"{CONFIG.col_end}{row}"].number_format = "HH:mm"

    # Write each session
    for i, sess in enumerate(sessions):
        row = first_row + i
        start_cell = f"{CONFIG.col_start}{row}"
        end_cell = f"{CONFIG.col_end}{row}"
        span_cell = f"{CONFIG.col_span}{row}"

        ws[start_cell] = _time_to_excel_fraction(sess.start_time)
        ws[end_cell] = _time_to_excel_fraction(sess.end_time)
        # D = C - B as a formula so the workbook stays interactive
        ws[span_cell] = f"={end_cell}-{start_cell}"

        ws[start_cell].number_format = "HH:mm"
        ws[end_cell].number_format = "HH:mm"
        ws[span_cell].number_format = CONFIG.duration_format

    # B2 = SUM of all span cells
    span_range = f"{CONFIG.col_span}{first_row}:{CONFIG.col_span}{last_row}"
    ws[CONFIG.cell_total_span] = f"=SUM({span_range})"
    ws[CONFIG.cell_total_span].number_format = CONFIG.duration_format

    # B3 = first session start time
    if sessions:
        ws[CONFIG.cell_first_start] = f"={CONFIG.col_start}{first_row}"
        ws[CONFIG.cell_first_start].number_format = "HH:mm"
    else:
        ws[CONFIG.cell_first_start] = None

    # B4 (attendance comment) is intentionally not touched.

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    log.info("Saved spreadsheet: %s", output_path)

    return {
        "sessions_written": len(sessions),
        "template_capacity": template_capacity,
        "extra_rows_inserted": extra_needed,
        "output_path": str(output_path),
    }


def _expand_template(ws, anchor_row: int, n_extra: int) -> None:
    """
    Insert n_extra rows below `anchor_row`, copying anchor_row's per-cell
    formatting (font, fill, border, alignment, number_format) and column A
    numbering convention.
    """
    ws.insert_rows(anchor_row + 1, amount=n_extra)

    src_row = anchor_row
    for offset in range(1, n_extra + 1):
        dst_row = anchor_row + offset
        for col_idx in range(1, ws.max_column + 1):
            col = get_column_letter(col_idx)
            src = ws[f"{col}{src_row}"]
            dst = ws[f"{col}{dst_row}"]
            if src.has_style:
                dst.font = copy.copy(src.font)
                dst.fill = copy.copy(src.fill)
                dst.border = copy.copy(src.border)
                dst.alignment = copy.copy(src.alignment)
                dst.number_format = src.number_format
                dst.protection = copy.copy(src.protection)

        # Continue numbering in column A
        ws[f"{CONFIG.col_index}{dst_row}"] = dst_row - anchor_row + (
            src_row - CONFIG.first_session_row + 1
        )
