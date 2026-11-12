"""Electrical Load Schedule sections for Excel / Word exports (moved verbatim from main.py). No Qt."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def append_electrical_sheet_to_excel(excel_path: str, unit_rows: list[dict], summary: dict):
    """Append a formatted 'Electrical_Load_Schedule' sheet to an Excel file."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.load_workbook(excel_path)
        sheet_name = "Electrical_Schedule"
        if sheet_name in wb.sheetnames:
            del wb[sheet_name]
        ws = wb.create_sheet(title=sheet_name)
        ws.views.sheetView[0].showGridLines = True

        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        card_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
        highlight_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

        font_title = Font(name="Segoe UI", size=13, bold=True, color="1F4E79")
        font_hdr = Font(name="Segoe UI", size=9.5, bold=True, color="FFFFFF")
        font_bold = Font(name="Segoe UI", size=9.5, bold=True)
        font_regular = Font(name="Segoe UI", size=9.0)

        thin = Side(style='thin', color='D9D9D9')
        border_all = Border(left=thin, right=thin, top=thin, bottom=thin)

        # Title
        ws["A1"] = "HVAC Electrical Load Schedule & Cable Sizing (IEC 60364)"
        ws["A1"].font = font_title
        ws.row_dimensions[1].height = 24

        # Summary KPIs Banner
        ws["A3"] = f"Total Load: {summary['total_kw']:.2f} kW"
        ws["C3"] = f"Demand Current: {summary['demand_fla']:.1f} A"
        ws["E3"] = f"Main Breaker: {summary['main_mcb']} A"
        ws["G3"] = f"Generator: {summary['generator_kva']} kVA"
        for col_letter in ["A", "C", "E", "G"]:
            ws[f"{col_letter}3"].font = font_bold
            ws[f"{col_letter}3"].fill = highlight_fill

        # Table Headers
        headers = [
            "#", "System Tag", "Model", "Qty", "Elec kW", "Voltage",
            "Ph", "FLA (A)", "MCB Rating", "Cable Specification", "V-Drop %", "Status"
        ]
        ws.row_dimensions[5].height = 22
        for c_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=5, column=c_idx, value=h)
            cell.fill = header_fill
            cell.font = font_hdr
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border_all

        # Data Rows
        for r_idx, r in enumerate(unit_rows, start=6):
            ws.row_dimensions[r_idx].height = 19
            row_fill = card_fill if r_idx % 2 == 0 else None
            vals = [
                r_idx - 5,
                r.get("system", f"AC-{r_idx-5}"),
                r.get("model", "N/A"),
                r.get("qty", 1),
                round(r["kw_elec"], 2),
                f"{int(r['voltage'])} V",
                f"{r['phase']} Ph",
                round(r["fla"], 1),
                f"{r['mcb_size']} A",
                r["cable_desc"],
                f"{r['vdrop_pct']:.2f} %",
                "OK" if r["vdrop_ok"] else "High V-Drop"
            ]
            for c_idx, v in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=v)
                cell.font = font_regular
                cell.border = border_all
                if row_fill:
                    cell.fill = row_fill
                cell.alignment = Alignment(
                    horizontal="left" if c_idx in (2, 3, 10) else "center",
                    vertical="center"
                )

        # Autofit columns
        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = max(len(str(c.value or '')) for c in col)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        wb.save(excel_path)
    except Exception:
        logger.exception("Failed to append electrical sheet to Excel: %s", excel_path)
        raise


def append_electrical_section_to_docx(doc, unit_rows: list[dict], summary: dict):
    """Append the Electrical Load Schedule section to a Word Document."""
    try:
        from docx.shared import Pt, RGBColor, Inches

        doc.add_page_break()
        h = doc.add_heading("HVAC Electrical Load & Distribution Schedule", level=1)
        h.paragraph_format.space_before = Pt(8)
        h.paragraph_format.space_after = Pt(4)

        desc = doc.add_paragraph(
            f"Electrical sizing according to IEC 60364 & Egyptian Code. "
            f"Total Connected Load: {summary['total_kw']:.2f} kW | "
            f"Design Current: {summary['demand_fla']:.1f} A | "
            f"Main Breaker: {summary['main_mcb']} A | "
            f"Main Feeder: {summary['main_cable_desc']}."
        )
        desc.runs[0].font.size = Pt(9.5)

        cols = ["#", "Tag", "Model", "Qty", "kW", "Supply", "FLA", "MCB", "Cable Specification", "V-Drop"]
        tbl = doc.add_table(rows=1, cols=len(cols))
        tbl.style = "Light Grid Accent 1"
        hdr = tbl.rows[0].cells
        for i, name in enumerate(cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(8)

        for idx, r in enumerate(unit_rows, start=1):
            cells = tbl.add_row().cells
            cells[0].text = str(idx)
            cells[1].text = str(r.get("system", f"AC-{idx}"))
            cells[2].text = str(r.get("model", "—"))
            cells[3].text = str(r.get("qty", 1))
            cells[4].text = f"{r['kw_elec']:.2f}"
            cells[5].text = f"{int(r['voltage'])}V/{r['phase']}P"
            cells[6].text = f"{r['fla']:.1f}A"
            cells[7].text = f"{r['mcb_size']}A"
            cells[8].text = str(r["cable_desc"])
            cells[9].text = f"{r['vdrop_pct']:.1f}%"
            for c in cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(8)

        doc.add_paragraph()
        if summary.get("warnings"):
            for w in summary["warnings"]:
                p_w = doc.add_paragraph(w)
                p_w.runs[0].font.size = Pt(9)
                p_w.runs[0].font.color.rgb = RGBColor(0xB9, 0x1C, 0x1C)
    except Exception:
        logger.exception("Failed to append electrical section to Word document")
        raise
