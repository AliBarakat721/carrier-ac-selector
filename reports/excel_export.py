"""Excel styling for exported selection sheets (moved verbatim from main.py). No Qt."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def style_excel_file(path: str):
    """تنسيق ملف الإكسل المصدّر بشكل هندسي احترافي بدون إيموجي وبأعمدة متسعة."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.load_workbook(path)
        ws = wb.active
        ws.views.sheetView[0].showGridLines = True
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        row_alt_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
        thin_border = Border(
            left=Side(style='thin', color='D9D9D9'),
            right=Side(style='thin', color='D9D9D9'),
            top=Side(style='thin', color='D9D9D9'),
            bottom=Side(style='thin', color='D9D9D9')
        )

        ws.row_dimensions[1].height = 28
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border

        max_row = ws.max_row
        max_col = ws.max_column

        for row_idx in range(2, max_row + 1):
            ws.row_dimensions[row_idx].height = 20
            is_even = (row_idx % 2 == 0)
            for col_idx in range(1, max_col + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = thin_border
                cell.font = Font(name="Segoe UI", size=9.5)
                if is_even:
                    cell.fill = row_alt_fill

                col_name = str(ws.cell(row=1, column=col_idx).value or "")
                val = cell.value

                # محاذاة وتنسيق الأرقام
                if isinstance(val, (int, float)):
                    if any(k in col_name for k in ["Btu", "CFM", "Capacity"]):
                        cell.number_format = '#,##0'
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif any(k in col_name for k in ["Tons", "kW"]):
                        cell.number_format = '#,##0.00'
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif "%" in col_name or "Excess" in col_name or "Cover" in col_name:
                        cell.number_format = '0.0"%"'
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif any(k in col_name for k in ["EER", "Weight", "Sound", "Area"]):
                        cell.number_format = '0.00'
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    if any(k in col_name for k in ["System", "Units", "Type", "Status", "Sensible OK"]):
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")

        # ضبط اتساع الأعمدة تلقائياً
        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = 0
            for cell in col:
                v_str = str(cell.value or '')
                max_len = max(max_len, len(v_str))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        wb.save(path)
    except Exception:
        logger.exception("Failed to style Excel export: %s", path)
        raise
