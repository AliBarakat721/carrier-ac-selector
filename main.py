"""
Carrier AC Selector — HAP Report Analyzer
==========================================
Reads cooling load (BTU/hr) and airflow (CFM) from Hourly Analysis Program
(HAP) screenshots via OCR, then filters the Carrier AC database to show
every unit that meets or exceeds those requirements.

Improvements over v2:
  • Robust OCR (grayscale 3x, multi-psm, PDF text layer) + strict parser
  • MBH is the reference load; no silent wrong-value fallbacks
  • CFM coverage %, derating, Excess %, sensible check, numeric sorting
  • Logic lives in core.py / ocr_engine.py and is covered by pytest

Improvements over v1:
  • Background OCR thread (UI never freezes)
  • Better image pre-processing (contrast + sharpness + unsharp mask)
  • Image preview panel
  • Manual override inputs (when OCR fails)
  • Export results to Excel or CSV
  • EER, kW, Tons columns in results table
  • "Best match" row highlighted in green
  • Double-click any row to copy model number
  • Status bar with live result count
  • OCR debug tab (raw text for troubleshooting)
  • Clear / reset button
  • Dark theme throughout
"""

import sys
import os
import re
import logging
import errno
import tempfile
from contextlib import contextmanager
import pandas as pd
import core
import ocr_engine
import math

logger = logging.getLogger(__name__)


# Keep the data-building order separate from the user-facing Results layout.
_RESULTS_TABLE_BASE_COLUMNS = (
    "Category", "Series", "System Model", "Indoor Unit", "Outdoor Unit",
    "Cooling (Btu/hr)", "Cooling (Tons)", "Cooling (kW)", "Excess %",
    "Airflow (CFM)", "CFM Cover %", "Sensible", "EER", "Type", "Power Supply",
)
_RESULTS_TABLE_COLUMNS = (
    "Category", "Series", "System Model", "Cooling (Btu/hr)", "Excess %",
    "Airflow (CFM)", "CFM Cover %", "EER", "Sensible", "Cooling (Tons)",
    "Cooling (kW)", "Indoor Unit", "Outdoor Unit", "Type", "Power Supply",
)
_RESULTS_TABLE_COLUMN_ORDER = tuple(
    _RESULTS_TABLE_BASE_COLUMNS.index(name) for name in _RESULTS_TABLE_COLUMNS
)


def _reorder_results_table_cells(cells):
    """Reorder base-order cell specs to match the visible Results headers."""
    if len(cells) != len(_RESULTS_TABLE_COLUMN_ORDER):
        raise ValueError(
            "Results cell count must match the Results table column count."
        )
    return [cells[index] for index in _RESULTS_TABLE_COLUMN_ORDER]


def _window_sizes_for_available_geometry(width: int, height: int):
    """Return (width, height, min_width, min_height) in Qt logical pixels."""
    if width <= 0 or height <= 0:
        raise ValueError("Available screen dimensions must be positive.")

    target_width = max(1, min(1300, int(width * 0.95)))
    target_height = max(1, min(820, int(height * 0.92)))
    min_width = min(850, target_width)
    min_height = min(560, target_height)
    return target_width, target_height, min_width, min_height


@contextmanager
def _atomic_export(path: str):
    """Write an export beside the final path and replace it only on success."""
    final_path = os.path.abspath(path)
    directory = os.path.dirname(final_path) or "."
    suffix = os.path.splitext(final_path)[1] or ".tmp"
    fd, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(final_path)}.",
        suffix=suffix,
        dir=directory,
    )
    os.close(fd)
    try:
        yield temporary_path
        os.replace(temporary_path, final_path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _replace_export_extension(path: str, required_ext: str) -> str:
    """Replace only a known export suffix; preserve dots in names like AHU 1.2."""
    base, suffix = os.path.splitext(path)
    if suffix.lower() in {".xlsx", ".docx", ".csv"}:
        path = base
    return f"{path}.{required_ext.lstrip('.')}"


def _export_error_message(exc: Exception) -> str:
    """Explain common Windows file-lock failures in terms users can fix."""
    if (isinstance(exc, PermissionError)
            or getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM, errno.EBUSY}):
        return (
            "The export could not replace the destination file. If it is open in "
            "Excel or Word, close it and try again; also check that you have "
            "permission to write to the selected folder."
        )
    return str(exc)

# ── Inverter ROI tab (optional module) ────────────────────────────
try:
    import roi_tab
    from roi_tab import (
        InverterROICalculator,
        append_roi_sheet_to_excel,
        append_roi_section_to_docx,
    )
    ROI_AVAILABLE = True
except ImportError as _roi_imp_err:
    ROI_AVAILABLE = False
    _ROI_IMPORT_ERROR = str(_roi_imp_err)

# ── Electrical Load module (optional) ─────────────────────────────
try:
    import electrical_engine as ee
    ELEC_AVAILABLE = True
except ImportError as _elec_imp_err:
    ELEC_AVAILABLE = False
    _ELEC_IMPORT_ERROR = str(_elec_imp_err)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QLabel,
    QFileDialog, QVBoxLayout, QHBoxLayout, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QGridLayout, QSplitter, QGroupBox,
    QStatusBar, QTextEdit, QTabWidget, QLineEdit, QProgressBar, QMenu,
    QAbstractItemView, QScrollArea, QFrame,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import (QPixmap, QColor, QFont, QBrush, QImage,
                              QShortcut, QKeySequence)

# Word report support
try:
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# PDF support (optional) - pure Python wheel, no system deps
try:
    import pymupdf as fitz
    PDF_AVAILABLE = True
except ImportError:
    try:
        import fitz  # older pymupdf
        PDF_AVAILABLE = True
    except ImportError:
        PDF_AVAILABLE = False

# ── Dark Theme ─────────────────────────────────────────────────────────────────
DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1a1b2e;
    color: #e2e8f0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QPushButton {
    background-color: #1d4ed8;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
}
QPushButton:hover  { background-color: #2563eb; }
QPushButton:pressed{ background-color: #1e40af; }
QPushButton:disabled { background-color: #374151; color: #6b7280; }
QPushButton#btn_success { background-color: #059669; }
QPushButton#btn_success:hover { background-color: #10b981; }
QPushButton#btn_warn { background-color: #b45309; }
QPushButton#btn_warn:hover { background-color: #d97706; }
QPushButton#btn_pick { background-color: #7c3aed; padding: 6px 10px; font-size: 12px; }
QPushButton#btn_pick:hover { background-color: #8b5cf6; }
QPushButton#btn_danger { background-color: #b91c1c; }
QPushButton#btn_danger:hover { background-color: #dc2626; }
QTableWidget {
    background-color: #111827;
    alternate-background-color: #1f2937;
    color: #e2e8f0;
    gridline-color: #374151;
    border: 1px solid #374151;
    border-radius: 6px;
}
QTableWidget::item:selected { background-color: #1e3a5f; color: #93c5fd; }
QHeaderView::section {
    background-color: #1f2937;
    color: #93c5fd;
    padding: 7px 4px;
    border: none;
    border-bottom: 2px solid #2563eb;
    font-weight: bold;
    font-size: 12px;
}
QComboBox, QSpinBox, QLineEdit {
    background-color: #1f2937;
    color: #e2e8f0;
    border: 1px solid #374151;
    border-radius: 4px;
    padding: 5px 8px;
    min-height: 28px;
}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus { border-color: #2563eb; }
QComboBox QAbstractItemView {
    background-color: #1f2937;
    color: #e2e8f0;
    selection-background-color: #1e3a5f;
    border: 1px solid #374151;
}
QComboBox::drop-down { border: none; }
QGroupBox {
    border: 1px solid #374151;
    border-radius: 6px;
    margin-top: 10px;
    padding: 8px 6px;
    color: #93c5fd;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    background-color: #1a1b2e;
}
QLabel { color: #e2e8f0; }
QLabel#lbl_app_title {
    color: #60a5fa;
    font-size: 17px;
    font-weight: bold;
}
QLabel#lbl_extracted {
    color: #34d399;
    font-weight: bold;
    font-size: 12px;
}
QStatusBar {
    background-color: #111827;
    color: #9ca3af;
    border-top: 1px solid #374151;
    padding: 3px 6px;
}
QTabWidget::pane { border: 1px solid #374151; border-radius: 4px; }
QTabBar::tab {
    background-color: #1f2937;
    color: #9ca3af;
    padding: 7px 14px;
}
QTabBar::tab:selected {
    background-color: #111827;
    color: #3b82f6;
    border-bottom: 2px solid #3b82f6;
}
QTextEdit {
    background-color: #0f172a;
    color: #86efac;
    border: 1px solid #374151;
    border-radius: 4px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
}
QScrollBar:vertical {
    background-color: #1a1b2e;
    width: 8px;
}
QScrollBar::handle:vertical {
    background-color: #374151;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover { background-color: #4b5563; }
QScrollBar:horizontal {
    background-color: #1a1b2e;
    height: 8px;
}
QScrollBar::handle:horizontal {
    background-color: #374151;
    border-radius: 4px;
    min-width: 20px;
}
QScrollBar::handle:horizontal:hover { background-color: #4b5563; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QProgressBar {
    background-color: #1f2937;
    border: 1px solid #374151;
    border-radius: 3px;
    text-align: center;
    color: transparent;
    max-height: 6px;
}
QProgressBar::chunk { background-color: #3b82f6; border-radius: 3px; }
"""

# ── Numeric-aware table item (so "9,000" < "12,100" when sorting) ─────────────
class NumItem(QTableWidgetItem):
    def __init__(self, text, value=None):
        super().__init__(text)
        self._v = value

    def __lt__(self, other):
        if (isinstance(other, NumItem) and self._v is not None
                and other._v is not None):
            return self._v < other._v
        return super().__lt__(other)


# ── Background OCR Thread ──────────────────────────────────────────────────────
class OCRWorker(QThread):
    """Runs OCR (ocr_engine) off the UI thread. Accepts images and PDFs."""
    done     = Signal(str)
    error    = Signal(str)
    progress = Signal(int)

    def __init__(self, source_path: str):
        super().__init__()
        self.source_path = source_path

    def run(self):
        try:
            text = ocr_engine.extract_text(self.source_path,
                                           progress=self.progress.emit)
            self.progress.emit(95)
            self.done.emit(text)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Main Application ───────────────────────────────────────────────────────────

def _style_excel_file(path: str):
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

# ══════════════════════════════════════════════════════════════
#  Electrical Load Schedule (merged from electrical_tab.py)
# ══════════════════════════════════════════════════════════════

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


class ElectricalScheduleTab(QWidget):
    """Interactive GUI Tab for Electrical Load Schedule calculation."""
    schedule_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_units_raw = []
        self._build_ui()
        self.recompute()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # ── Row 1: Parameters & Summary Cards ────
        top_hbox = QHBoxLayout()
        top_hbox.setSpacing(10)

        # Input Parameters Group
        grp_param = QGroupBox("⚙️  Electrical Parameters")
        p_lyt = QGridLayout(grp_param)
        p_lyt.setSpacing(6)
        p_lyt.setContentsMargins(8, 8, 8, 8)

        p_lyt.addWidget(QLabel("Power Factor (cos φ):"), 0, 0)
        self.spin_pf = QDoubleSpinBox()
        self.spin_pf.setRange(0.70, 0.99)
        self.spin_pf.setValue(0.85)
        self.spin_pf.setSingleStep(0.02)
        self.spin_pf.valueChanged.connect(self.recompute)
        p_lyt.addWidget(self.spin_pf, 0, 1)

        p_lyt.addWidget(QLabel("Diversity Factor:"), 1, 0)
        self.spin_div = QDoubleSpinBox()
        self.spin_div.setRange(0.50, 1.00)
        self.spin_div.setValue(0.90)
        self.spin_div.setSingleStep(0.05)
        self.spin_div.valueChanged.connect(self.recompute)
        p_lyt.addWidget(self.spin_div, 1, 1)

        p_lyt.addWidget(QLabel("Ambient Temp (°C):"), 2, 0)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(25.0, 55.0)
        self.spin_temp.setValue(40.0)
        self.spin_temp.setSuffix(" °C")
        self.spin_temp.valueChanged.connect(self.recompute)
        p_lyt.addWidget(self.spin_temp, 2, 1)

        p_lyt.addWidget(QLabel("Distance to Panel:"), 3, 0)
        self.spin_dist = QDoubleSpinBox()
        self.spin_dist.setRange(5.0, 150.0)
        self.spin_dist.setValue(20.0)
        self.spin_dist.setSuffix(" m")
        self.spin_dist.valueChanged.connect(self.recompute)
        p_lyt.addWidget(self.spin_dist, 3, 1)

        p_lyt.addWidget(QLabel("Installation Method:"), 4, 0)
        self.combo_method = QComboBox()
        self.combo_method.addItems(["Conduit", "Tray", "Buried"])
        self.combo_method.currentIndexChanged.connect(self.recompute)
        p_lyt.addWidget(self.combo_method, 4, 1)

        top_hbox.addWidget(grp_param, 2)

        # Summary KPIs Group
        grp_summary = QGroupBox("📊  Main Feeder & Panel Sizing")
        s_lyt = QGridLayout(grp_summary)
        s_lyt.setSpacing(6)
        s_lyt.setContentsMargins(8, 8, 8, 8)

        self.lbl_sum_kw = QLabel("0.0 kW")
        self.lbl_sum_amp = QLabel("0.0 A")
        self.lbl_main_mcb = QLabel("—")
        self.lbl_main_cable = QLabel("—")
        self.lbl_generator = QLabel("0 kVA")
        self.lbl_panel_size = QLabel("—")

        for lbl, col in [(self.lbl_sum_kw, "#60a5fa"), (self.lbl_sum_amp, "#34d399"),
                         (self.lbl_main_mcb, "#fbbf24"), (self.lbl_main_cable, "#a78bfa"),
                         (self.lbl_generator, "#38bdf8"), (self.lbl_panel_size, "#e2e8f0")]:
            lbl.setStyleSheet(f"color: {col}; font-weight: bold; font-size: 13px;")

        s_lyt.addWidget(QLabel("Total Connected Load:"), 0, 0)
        s_lyt.addWidget(self.lbl_sum_kw, 0, 1)
        s_lyt.addWidget(QLabel("Design Demand Current:"), 1, 0)
        s_lyt.addWidget(self.lbl_sum_amp, 1, 1)
        s_lyt.addWidget(QLabel("Main Incomer MCB:"), 2, 0)
        s_lyt.addWidget(self.lbl_main_mcb, 2, 1)
        s_lyt.addWidget(QLabel("Main Feeder Cable:"), 3, 0)
        s_lyt.addWidget(self.lbl_main_cable, 3, 1)
        s_lyt.addWidget(QLabel("Standby Generator:"), 4, 0)
        s_lyt.addWidget(self.lbl_generator, 4, 1)
        s_lyt.addWidget(QLabel("Recommended Panel:"), 5, 0)
        s_lyt.addWidget(self.lbl_panel_size, 5, 1)

        top_hbox.addWidget(grp_summary, 3)
        root.addLayout(top_hbox)

        # ── Row 2: Unit-by-Unit Schedule Table ────
        grp_table = QGroupBox("📋  Unit-by-Unit Electrical Distribution Schedule")
        t_lyt = QVBoxLayout(grp_table)
        t_lyt.setContentsMargins(6, 6, 6, 6)

        self.table = QTableWidget()
        cols = ["#", "System Tag", "Model", "Qty", "Power (kW)", "Supply", "FLA (A)", "MCB Size", "Cable Sizing", "V-Drop", "Status"]
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        t_lyt.addWidget(self.table)

        self.lbl_warnings = QLabel("")
        self.lbl_warnings.setWordWrap(True)
        self.lbl_warnings.setStyleSheet("color: #f87171; font-weight: bold; font-size: 11px; padding: 2px;")
        t_lyt.addWidget(self.lbl_warnings)

        root.addWidget(grp_table)

        # ── Row 3: Bottom Actions ────
        bot_row = QHBoxLayout()
        bot_row.setSpacing(10)

        self.chk_excel = QCheckBox("Include Electrical Schedule in Excel")
        self.chk_excel.setChecked(True)
        bot_row.addWidget(self.chk_excel)

        self.chk_word = QCheckBox("Include Electrical Section in Word")
        self.chk_word.setChecked(True)
        bot_row.addWidget(self.chk_word)

        bot_row.addStretch()

        btn_copy = QPushButton("📋  Copy Schedule (MD)")
        btn_copy.setObjectName("btn_pick")
        btn_copy.clicked.connect(self._copy_markdown)
        bot_row.addWidget(btn_copy)

        btn_export = QPushButton("💾  Export Electrical Sheet")
        btn_export.setObjectName("btn_success")
        btn_export.clicked.connect(self._export_standalone)
        bot_row.addWidget(btn_export)

        root.addLayout(bot_row)

    def is_excel_export_enabled(self) -> bool:
        return self.chk_excel.isChecked()

    def is_word_export_enabled(self) -> bool:
        return self.chk_word.isChecked()

    def sync_units(self, selected_dict: dict, fallback_btu: float = 0.0, fallback_ps: str = "220V/1Ph"):
        """Called by main.py whenever Cart/Selected units update."""
        raw = []
        if selected_dict:
            for sys_name, entry in selected_dict.items():
                r = entry["row"]
                raw.append({
                    "system": sys_name,
                    "model": str(r.get("System Model", "N/A")),
                    "btu": core.to_float(r.get("Cooling Capacity (Btu/hr)")) or 0.0,
                    "kw":  core.to_float(r.get("Cooling Capacity (kW)")) or 0.0,
                    "eer": core.to_float(r.get("EER (Btu/Wh)")) or 0.0,
                    "ps": str(r.get("Power Supply", "220V / 1Ph")),
                    "qty": int(entry.get("units", 1)),
                    "power_w":   core.to_float(r.get("Cooling Power Input (W)")),
                    "current_a": core.to_float(r.get("Cooling Current (A)")),
                })
        elif fallback_btu > 0:
            raw.append({
                "system": "Active System",
                "model": "Selected AC",
                "btu": fallback_btu,
                "kw": fallback_btu / 3412.14,
                "eer": 10.5,
                "ps": fallback_ps,
                "qty": 1,
            })
        self.current_units_raw = raw
        self.recompute()

    def recompute(self):
        pf = self.spin_pf.value()
        div = self.spin_div.value()
        dist = self.spin_dist.value()
        temp = self.spin_temp.value()
        method = self.combo_method.currentText()

        unit_results = []
        for u in self.current_units_raw:
            calc = ee.calculate_unit_electrical(
                cooling_btu=u["btu"],
                cooling_kw=u["kw"],
                eer=u["eer"],
                power_supply_str=u["ps"],
                distance_m=dist,
                power_factor=pf,
                ambient_temp_c=temp,
                install_method=method,
                power_w_catalog=u.get("power_w"),
                current_a_catalog=u.get("current_a"),
            )
            calc["system"] = u["system"]
            calc["model"] = u["model"]
            calc["qty"] = u["qty"]
            unit_results.append(calc)

        summary = ee.compute_schedule_summary(
            unit_results,
            diversity_factor=div,
            power_factor=pf,
            distance_to_main_m=dist + 5.0,
            ambient_temp_c=temp,
        )

        # Update KPI Labels
        self.lbl_sum_kw.setText(f"{summary['total_kw']:.2f} kW")
        self.lbl_sum_amp.setText(f"{summary['demand_fla']:.1f} A")
        self.lbl_main_mcb.setText(f"{summary['main_mcb']} A ({'3Ph' if summary['is_three_phase_panel'] else '1Ph'})")
        self.lbl_main_cable.setText(summary["main_cable_desc"])
        self.lbl_generator.setText(f"{summary['generator_kva']} kVA")
        self.lbl_panel_size.setText(f"{summary['panel_ways']}-Way Panel")

        # Update Table
        self.table.setRowCount(len(unit_results))
        for r_i, r in enumerate(unit_results):
            cells = [
                str(r_i + 1),
                str(r["system"]),
                str(r["model"]),
                f"{r['qty']}x",
                f"{r['kw_elec']:.2f}",
                f"{int(r['voltage'])}V/{r['phase']}Ph",
                f"{r['fla']:.1f}",
                f"{r['mcb_size']} A",
                r["cable_desc"],
                f"{r['vdrop_pct']:.2f}%",
                "✅ OK" if r["vdrop_ok"] else "⚠️ High",
            ]
            for c_i, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                item.setTextAlignment(Qt.AlignCenter if c_i not in (1, 2, 8) else (Qt.AlignLeft | Qt.AlignVCenter))
                if c_i == 10 and not r["vdrop_ok"]:
                    item.setForeground(QBrush(QColor("#f87171")))
                self.table.setItem(r_i, c_i, item)

        # Warnings
        self.lbl_warnings.setText("  •  ".join(summary["warnings"]) if summary["warnings"] else "")

        self.last_results = (unit_results, summary)
        self.schedule_changed.emit({"units": unit_results, "summary": summary})

    def _copy_markdown(self):
        if not hasattr(self, "last_results"):
            return
        units, summary = self.last_results
        md = ee.format_electrical_markdown(units, summary)
        QApplication.clipboard().setText(md)
        QMessageBox.information(self, "Copied", "Electrical Schedule Markdown copied to clipboard!")

    def _export_standalone(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Electrical Schedule", "Electrical_Load_Schedule.xlsx", "Excel Workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        units, summary = self.last_results
        try:
            with _atomic_export(path) as temporary_path:
                pd.DataFrame({"Project": ["Carrier Electrical Schedule"]}).to_excel(
                    temporary_path, index=False
                )
                append_electrical_sheet_to_excel(temporary_path, units, summary)
        except Exception as exc:
            logger.exception("Standalone electrical export failed: %s", path)
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))
            return
        QMessageBox.information(self, "Saved", f"Electrical Schedule saved successfully to:\n{path}")


class HAPToCarrierApp(QMainWindow):
    # Tolerances for "did the value change?" checks
    _EPS_BTU = 0.5   # 0.5 Btu/hr is negligible
    _EPS_CFM = 0.1   # 0.5 CFM was too loose — it hid real edits

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Carrier AC Selector  —  HAP Report Analyzer")

        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        available_width = available.width() if available else 1300
        available_height = available.height() if available else 820
        width, height, min_width, min_height = (
            _window_sizes_for_available_geometry(
                available_width, available_height
            )
        )
        self.setMinimumSize(min_width, min_height)
        if available:
            x = available.x() + (available.width() - width) // 2
            y = available.y() + (available.height() - height) // 2
            self.setGeometry(x, y, width, height)
        else:
            self.resize(width, height)

        # ── Load database ──────────────────────────────────────
        base_dir = os.path.dirname(os.path.abspath(__file__))
        db_path  = core.find_database(base_dir)
        if db_path is None:
            QMessageBox.critical(
                self, "Database Not Found",
                "Carrier_AC_Database.xlsx / .csv was not found in:\n"
                f"{os.path.join(base_dir, 'data')}\n{base_dir}")
            sys.exit(1)
        try:
            self.df = core.load_database(db_path)
        except Exception as exc:
            QMessageBox.critical(self, "Database Error", str(exc))
            sys.exit(1)

        # ── Internal state ─────────────────────────────────────
        self.current_btu     = 0.0
        self.current_cfm     = 0.0
        self.current_sensible = 0.0   # sensible coil load (Btu/hr), 0 = unknown
        self.current_results = None   # DataFrame of last filtered results
        self.ocr_worker      = None
        self.source_basename = ""     # basename of uploaded file (e.g. "G01")
        self.current_system_name = ""  # active HAP system (e.g. "G01")
        self.picked_df_index = None   # index of the last "Pick Best" row
        self.systems         = []     # list[core.SystemLoad] from last report
        self.batch_results   = None   # DataFrame summary of all systems
        self._updating_batch  = False  # re-entrancy guard for _render_batch
        self._batch_cache_key = None   # invalidation key for batch cache
        self._nav_silent      = False  # True while Prev/Next changes selection
        # ── [NEW/OPTIONAL] Selection cart — purely additive ──
        self.selected_units   = {}     # {system_name: pd.Series}

        self._build_ui()
        self.setAcceptDrops(True)

        notes = []
        if not DOCX_AVAILABLE:
            notes.append("Word export disabled (pip install python-docx)")
        if not ocr_engine.PDF_AVAILABLE:
            notes.append("PDF input disabled (pip install pymupdf)")
        if notes:
            self._set_status("ℹ  " + "  |  ".join(notes))

    # ──────────────────────────────────────────────────────────────
    # UI Construction
    # ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.setStyleSheet(DARK_STYLESHEET)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._set_status("Ready — upload a HAP screenshot or enter values manually.")

        # ── Root layout: horizontal splitter (left | right) ────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([300, 980])

        self.setCentralWidget(splitter)

    # ── Left panel ─────────────────────────────────────────────
    def _build_left_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(0)

        panel = QWidget()
        panel.setMinimumWidth(0)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 6, 10)
        layout.setSpacing(10)
        panel.setMaximumWidth(330)

        # App title
        title = QLabel("🌡️  Carrier AC Selector")
        title.setObjectName("lbl_app_title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("HAP Report Analyzer  •  v2.0")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(subtitle)

        # Upload button
        self.btn_upload = QPushButton("📁  Upload HAP Report")
        self.btn_upload.setMinimumHeight(42)
        self.btn_upload.clicked.connect(self.upload_image)
        layout.addWidget(self.btn_upload)

        # OCR progress bar (hidden until active)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Image preview
        img_grp = QGroupBox("Image Preview")
        img_lyt = QVBoxLayout(img_grp)
        self.lbl_image = QLabel("No image loaded")
        self.lbl_image.setAlignment(Qt.AlignCenter)
        self.lbl_image.setFixedHeight(210)
        self.lbl_image.setStyleSheet(
            "color: #4b5563; border: 2px dashed #374151; border-radius: 6px;"
        )
        img_lyt.addWidget(self.lbl_image)
        layout.addWidget(img_grp)

        # Extracted values
        ext_grp = QGroupBox("Extracted Load Data (from OCR)")
        ext_lyt = QVBoxLayout(ext_grp)
        self.lbl_btu = QLabel("Cooling Load:  —")
        self.lbl_cfm = QLabel("Airflow:  —")
        for lbl in (self.lbl_btu, self.lbl_cfm):
            lbl.setObjectName("lbl_extracted")
            lbl.setWordWrap(True)
            ext_lyt.addWidget(lbl)
        layout.addWidget(ext_grp)

        # Verify values (unified OCR + manual entry)
        verify_grp = QGroupBox("Verify Values  (edit if wrong, then Confirm)")
        verify_lyt = QVBoxLayout(verify_grp)

        row_btu = QHBoxLayout()
        row_btu.addWidget(QLabel("BTU/hr:"))
        self.input_btu = QLineEdit()
        self.input_btu.setPlaceholderText("e.g. 82800")
        self.input_btu.setToolTip("Cooling load in Btu/hr. OCR fills this automatically.")
        row_btu.addWidget(self.input_btu)
        verify_lyt.addLayout(row_btu)

        row_cfm = QHBoxLayout()
        row_cfm.addWidget(QLabel("CFM:      "))
        self.input_cfm = QLineEdit()
        self.input_cfm.setPlaceholderText("e.g. 2663")
        self.input_cfm.setToolTip("Airflow in CFM. OCR fills this automatically.")
        row_cfm.addWidget(self.input_cfm)
        verify_lyt.addLayout(row_cfm)

        row_sens = QHBoxLayout()
        row_sens.addWidget(QLabel("Sensible:"))
        self.input_sensible = QLineEdit()
        self.input_sensible.setPlaceholderText("e.g. 66000  (optional)")
        self.input_sensible.setToolTip(
            "Sensible coil load in Btu/hr (from HAP). Optional — used only "
            "for the informational 'Sensible OK' column, never to filter.")
        row_sens.addWidget(self.input_sensible)
        verify_lyt.addLayout(row_sens)

        # Small hint label that shows OCR status
        self.lbl_ocr_status = QLabel("Waiting for OCR…")
        self.lbl_ocr_status.setWordWrap(True)
        self.lbl_ocr_status.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 2px 0;")
        verify_lyt.addWidget(self.lbl_ocr_status)

        self.btn_confirm = QPushButton("✔  Confirm & Search")
        self.btn_confirm.setObjectName("btn_success")
        self.btn_confirm.setMinimumHeight(36)
        self.btn_confirm.clicked.connect(self.confirm_and_search)
        verify_lyt.addWidget(self.btn_confirm)

        layout.addWidget(verify_grp)

        # Clear button
        self.btn_clear = QPushButton("✕  Clear All")
        self.btn_clear.setObjectName("btn_danger")
        self.btn_clear.clicked.connect(self.clear_all)
        layout.addWidget(self.btn_clear)

        layout.addStretch()
        scroll.setWidget(panel)
        return scroll

    # ── Right panel ────────────────────────────────────────────
    def _build_right_panel(self) -> QWidget:
        panel  = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 10, 10, 10)
        layout.setSpacing(8)

        # ── Filter bar ─────────────────────────────────────────
        filter_grp = QGroupBox("Filters & Options")
        filter_outer = QVBoxLayout(filter_grp)
        filter_outer.setSpacing(6)
        filter_lyt = QGridLayout()
        filter_lyt.setHorizontalSpacing(8)
        filter_lyt.setVerticalSpacing(6)
        filter_lyt.setColumnStretch(1, 1)
        filter_lyt.setColumnStretch(3, 1)

        filter_lyt.addWidget(QLabel("Category:"), 0, 0)
        self.combo_category = QComboBox()
        self.combo_category.addItems(
            ["All Categories"] + sorted(self.df['Category'].dropna().unique().tolist())
        )
        self.combo_category.currentIndexChanged.connect(self.apply_filters)
        filter_lyt.addWidget(self.combo_category, 0, 1)

        filter_lyt.addWidget(QLabel("Type:"), 0, 2)
        self.combo_type = QComboBox()
        self.combo_type.addItems(
            ["All Types"] + sorted(self.df['Type'].dropna().unique().tolist())
        )
        self.combo_type.currentIndexChanged.connect(self.apply_filters)
        filter_lyt.addWidget(self.combo_type, 0, 3)

        filter_lyt.addWidget(QLabel("Series:"), 1, 0)
        self.combo_series = QComboBox()
        series_set = set()
        for s in self.df['Series'].dropna():
            for item in str(s).split('/'):
                item = item.strip()
                if item:
                    series_set.add(item)
        self.combo_series.addItems(["All Series"] + sorted(series_set))
        self.combo_series.currentIndexChanged.connect(self.apply_filters)
        filter_lyt.addWidget(self.combo_series, 1, 1)

        filter_lyt.addWidget(QLabel("Power:"), 1, 2)
        self.combo_power = QComboBox()
        self.combo_power.addItems(
            ["All Supplies"] + sorted(self.df['Power Supply'].dropna().unique().tolist())
        )
        self.combo_power.currentIndexChanged.connect(self.apply_filters)
        filter_lyt.addWidget(self.combo_power, 1, 3)

        filter_outer.addLayout(filter_lyt)

        filter_row2 = QGridLayout()
        filter_row2.setHorizontalSpacing(8)
        filter_row2.setVerticalSpacing(6)
        filter_row2.setColumnStretch(1, 1)
        filter_row2.setColumnStretch(3, 1)
        filter_row2.setColumnStretch(5, 1)

        filter_row2.addWidget(QLabel("Safety Factor:"), 0, 0)
        self.spin_safety = QSpinBox()
        self.spin_safety.setRange(0, 30)
        self.spin_safety.setValue(0)
        self.spin_safety.setSuffix(" %")
        self.spin_safety.setFixedWidth(82)
        self.spin_safety.setToolTip("Extra margin added to the required load.")
        self.spin_safety.valueChanged.connect(self.apply_filters)
        filter_row2.addWidget(self.spin_safety, 0, 1)

        filter_row2.addWidget(QLabel("Min CFM:"), 0, 2)
        self.spin_cover = QSpinBox()
        self.spin_cover.setRange(50, 100)
        self.spin_cover.setValue(100)
        self.spin_cover.setSuffix(" %")
        self.spin_cover.setFixedWidth(82)
        self.spin_cover.setToolTip(
            "Share of the HAP coil CFM that the unit's HIGH speed must deliver.\n"
            "HAP CFM is a design value (55 °F supply); 85–100 % is typical.")
        self.spin_cover.valueChanged.connect(self.apply_filters)
        filter_row2.addWidget(self.spin_cover, 0, 3)

        filter_row2.addWidget(QLabel("Derating:"), 0, 4)
        self.spin_derate = QSpinBox()
        self.spin_derate.setRange(0, 30)
        self.spin_derate.setValue(0)
        self.spin_derate.setSuffix(" %")
        self.spin_derate.setFixedWidth(82)
        self.spin_derate.setToolTip(
            "Capacity loss at design ambient vs the catalogue rating "
            "(e.g. Cairo OA ≈ 97 °F).")
        self.spin_derate.valueChanged.connect(self.apply_filters)
        filter_row2.addWidget(self.spin_derate, 0, 5)

        # Export actions occupy a separate row on narrow windows.

        self.btn_export = QPushButton("💾  Export All")
        self.btn_export.setObjectName("btn_success")
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip("Export every row currently shown in the table.")
        self.btn_export.clicked.connect(self.export_results)
        filter_row2.addWidget(self.btn_export, 1, 0, 1, 3)

        self.btn_export_sel = QPushButton("💾  Export Selected")
        self.btn_export_sel.setObjectName("btn_success")
        self.btn_export_sel.setEnabled(False)
        self.btn_export_sel.setToolTip(
            "Select rows in the table (Ctrl+Click / Shift+Click), "
            "then click to export only those."
        )
        self.btn_export_sel.clicked.connect(self.export_selected)
        filter_row2.addWidget(self.btn_export_sel, 1, 3, 1, 3)

        filter_outer.addLayout(filter_row2)

        layout.addWidget(filter_grp)

        # ── Tabs: Results | OCR Debug ──────────────────────────
        self.tabs = QTabWidget()

        # ── Results tab ────────────────────────────────────────
        results_tab = QWidget()
        results_lyt = QVBoxLayout(results_tab)
        results_lyt.setContentsMargins(0, 4, 0, 0)
        results_lyt.setSpacing(4)

        self.lbl_count = QLabel("No results yet — upload a HAP report to begin.")
        self.lbl_count.setStyleSheet("color: #6b7280; font-size: 12px; padding: 2px 4px;")
        results_lyt.addWidget(self.lbl_count)

        # ── Best-Unit picker ─────────────────────────────
        best_grp = QGroupBox("🏆  Pick the Best Unit")
        best_lyt = QGridLayout(best_grp)
        best_lyt.setHorizontalSpacing(6)
        best_lyt.setVerticalSpacing(6)

        self.btn_best_overall = QPushButton("🏆  Best Overall")
        self.btn_best_overall.setObjectName("btn_success")
        self.btn_best_overall.setToolTip(
            "Balanced score: least oversized + high EER + airflow closest to 100%.")
        self.btn_best_overall.clicked.connect(lambda: self._pick_best("overall"))
        best_lyt.addWidget(self.btn_best_overall, 0, 0)

        self.btn_best_size = QPushButton("💰  Least Oversized")
        self.btn_best_size.setObjectName("btn_pick")
        self.btn_best_size.setToolTip(
            "Smallest cooling capacity that still covers the required load.")
        self.btn_best_size.clicked.connect(lambda: self._pick_best("size"))
        best_lyt.addWidget(self.btn_best_size, 0, 1)

        self.btn_best_eer = QPushButton("⚡  Most Efficient")
        self.btn_best_eer.setObjectName("btn_pick")
        self.btn_best_eer.setToolTip("Highest EER (Btu/Wh) among all matches.")
        self.btn_best_eer.clicked.connect(lambda: self._pick_best("eer"))
        best_lyt.addWidget(self.btn_best_eer, 0, 2)

        self.btn_best_cfm = QPushButton("🌬  Best Airflow")
        self.btn_best_cfm.setObjectName("btn_pick")
        self.btn_best_cfm.setToolTip(
            "Closest CFM coverage to 100% (never under, not over by much).")
        self.btn_best_cfm.clicked.connect(lambda: self._pick_best("cfm"))
        best_lyt.addWidget(self.btn_best_cfm, 1, 0)

        best_lyt.setColumnStretch(0, 1)
        best_lyt.setColumnStretch(1, 1)
        best_lyt.setColumnStretch(2, 1)

        self.btn_pick_export = QPushButton("📄  Export Picked")
        self.btn_pick_export.setObjectName("btn_pick")
        self.btn_pick_export.setToolTip(
            "Export only the currently picked best unit as a report.")
        self.btn_pick_export.clicked.connect(self._export_picked)
        best_lyt.addWidget(self.btn_pick_export, 1, 1)

        # ── [NEW/OPTIONAL] Selection Cart ──
        self.btn_add_to_cart = QPushButton("➕  Add to Selection")
        self.btn_add_to_cart.setObjectName("btn_pick")
        self.btn_add_to_cart.setToolTip(
            "اختيارى: أضف الوحدة المختارة لسلة الاختيارات.\n"
            "بعد ما تختار وحدة لكل System، افتح تاب 🛒 Selected\n"
            "واحفظهم كلهم في ملف واحد.")
        self.btn_add_to_cart.clicked.connect(self._add_to_selection)
        best_lyt.addWidget(self.btn_add_to_cart, 1, 2)

        results_lyt.addWidget(best_grp)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(110)
        self.table.horizontalHeader().setMinimumSectionSize(60)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)   # ← Ctrl/Shift multi-select
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.itemDoubleClicked.connect(self._copy_model_on_dblclick)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_table_context_menu)
        results_lyt.addWidget(self.table)

        hint = QLabel(
            "💡  Ctrl+Click / Shift+Click to select multiple rows  •  "
            "Double-click to copy System Model  •  "
            "Then use “Export Selected”."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280; font-size: 11px; padding: 2px 4px;")
        results_lyt.addWidget(hint)

        self.tab_results = results_tab
        self.tabs.addTab(results_tab, "📋  Results")

        # ── Multi-Unit tab ─────────────────────────────────────
        multi_tab = QWidget()
        multi_lyt = QVBoxLayout(multi_tab)
        multi_lyt.setContentsMargins(0, 4, 0, 0)
        self.lbl_multi_hint = QLabel(
            "When no single unit covers the required load, this tab suggests "
            "the minimum number of identical units needed."
        )
        self.lbl_multi_hint.setWordWrap(True)
        self.lbl_multi_hint.setStyleSheet("color: #6b7280; font-size: 12px; padding: 2px 4px;")
        multi_lyt.addWidget(self.lbl_multi_hint)

        self.table_multi = QTableWidget()
        self.table_multi.setAlternatingRowColors(True)
        self.table_multi.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_multi.verticalHeader().setVisible(False)
        self.table_multi.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_multi.setSelectionMode(QTableWidget.SingleSelection)
        self.table_multi.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_multi.setSortingEnabled(True)
        multi_lyt.addWidget(self.table_multi)

        # زرار تصدير نتائج Multi-Unit
        multi_btn_row = QHBoxLayout()
        multi_btn_row.setSpacing(6)

        btn_multi_export = QPushButton("💾  Export Multi-Unit")
        btn_multi_export.setObjectName("btn_success")
        btn_multi_export.setToolTip(
            "Export the multi-unit combinations to Excel / Word / CSV.")
        btn_multi_export.clicked.connect(self._export_multi)
        multi_btn_row.addWidget(btn_multi_export)

        # ── [NEW/OPTIONAL] Add multi-unit option to cart ──
        self.btn_multi_add = QPushButton("➕  Add to Selection")
        self.btn_multi_add.setObjectName("btn_pick")
        self.btn_multi_add.setToolTip(
            "أضف الخيار المحدد (أو الأفضل) في الجدول لسلة الاختيارات.\n"
            "السلة هتحفظ عدد الوحدات (مثلاً 2x) مع الموديل.")
        self.btn_multi_add.clicked.connect(self._add_multi_to_selection)
        multi_btn_row.addWidget(self.btn_multi_add)

        multi_lyt.addLayout(multi_btn_row)

        self.tab_multi = multi_tab
        self.tabs.addTab(multi_tab, "🔢  Multi-Unit")

        # ── Systems (Batch) tab ────────────────────────────────
        batch_tab = QWidget()
        batch_lyt = QVBoxLayout(batch_tab)
        batch_lyt.setContentsMargins(0, 4, 0, 0)
        self.lbl_batch_hint = QLabel(
            "Upload a full HAP Systems Design Report — every air system is "
            "detected automatically and the best unit is picked for each.")
        self.lbl_batch_hint.setWordWrap(True)
        self.lbl_batch_hint.setStyleSheet(
            "color: #6b7280; font-size: 12px; padding: 2px 4px;")
        batch_lyt.addWidget(self.lbl_batch_hint)

        # ── Navigation row: Prev / Next / position / View Results ──
        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)

        self.btn_prev_sys = QPushButton("⬅  Previous")
        self.btn_prev_sys.setObjectName("btn_pick")
        self.btn_prev_sys.setToolTip("Previous system  (Ctrl+Up)")
        self.btn_prev_sys.clicked.connect(lambda: self._navigate_system(-1))
        nav_row.addWidget(self.btn_prev_sys)

        self.btn_next_sys = QPushButton("Next  ➡")
        self.btn_next_sys.setObjectName("btn_pick")
        self.btn_next_sys.setToolTip("Next system  (Ctrl+Down)")
        self.btn_next_sys.clicked.connect(lambda: self._navigate_system(+1))
        nav_row.addWidget(self.btn_next_sys)

        self.lbl_nav_pos = QLabel("—")
        self.lbl_nav_pos.setStyleSheet(
            "color: #93c5fd; font-size: 12px; font-weight: bold; padding: 0 8px;")
        self.lbl_nav_pos.setMinimumWidth(60)
        self.lbl_nav_pos.setAlignment(Qt.AlignCenter)
        nav_row.addWidget(self.lbl_nav_pos)

        self.btn_view_results = QPushButton("📋  View Results")
        self.btn_view_results.setObjectName("btn_success")
        self.btn_view_results.setToolTip(
            "Jump to the Results tab for the currently selected system.")
        self.btn_view_results.clicked.connect(self._view_results_from_systems)
        nav_row.addWidget(self.btn_view_results)

        nav_row.addStretch()
        batch_lyt.addLayout(nav_row)

        self.table_batch = QTableWidget()
        self.table_batch.setAlternatingRowColors(True)
        self.table_batch.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self.table_batch.horizontalHeader().setDefaultSectionSize(110)
        self.table_batch.horizontalHeader().setMinimumSectionSize(60)
        self.table_batch.horizontalHeader().setStretchLastSection(True)
        self.table_batch.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table_batch.verticalHeader().setVisible(False)
        self.table_batch.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_batch.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_batch.setSortingEnabled(True)
        batch_lyt.addWidget(self.table_batch)
        # Click a row to load its values into Verify Values (see handler).
        self.table_batch.setSelectionMode(QTableWidget.SingleSelection)
        self.table_batch.itemSelectionChanged.connect(
            self._on_batch_row_selected)

        btn_batch_export = QPushButton("💾  Export Systems Summary")
        btn_batch_export.setObjectName("btn_success")
        btn_batch_export.setToolTip(
            "Export one row per system: load + best matching unit.")
        btn_batch_export.clicked.connect(self._export_batch)
        batch_lyt.addWidget(btn_batch_export)

        self.tab_systems = batch_tab
        self.tabs.addTab(batch_tab, "🏢  Systems")

        # ── [NEW/OPTIONAL] Selected (Cart) tab ──
        selected_tab = QWidget()
        selected_lyt = QVBoxLayout(selected_tab)
        selected_lyt.setContentsMargins(0, 4, 0, 0)
        selected_lyt.setSpacing(4)

        self.lbl_selected_hint = QLabel(
            "اختيارى:  اختار وحدة من Results واضغط “Add to Selection” "
            "عشان تضيفها هنا.")
        self.lbl_selected_hint.setWordWrap(True)
        self.lbl_selected_hint.setStyleSheet(
            "color: #6b7280; font-size: 12px; padding: 2px 4px;")
        selected_lyt.addWidget(self.lbl_selected_hint)

        self.table_selected = QTableWidget()
        self.table_selected.setAlternatingRowColors(True)
        self.table_selected.horizontalHeader().setSectionResizeMode(
            QHeaderView.Interactive)
        self.table_selected.horizontalHeader().setDefaultSectionSize(110)
        self.table_selected.horizontalHeader().setMinimumSectionSize(60)
        self.table_selected.horizontalHeader().setStretchLastSection(True)
        self.table_selected.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table_selected.verticalHeader().setVisible(False)
        self.table_selected.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_selected.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table_selected.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_selected.setSortingEnabled(True)
        selected_lyt.addWidget(self.table_selected)

        cart_btn_row = QHBoxLayout()
        cart_btn_row.setSpacing(6)

        self.btn_remove_cart = QPushButton("🗑  Remove Selected")
        self.btn_remove_cart.setObjectName("btn_warn")
        self.btn_remove_cart.setEnabled(False)
        self.btn_remove_cart.clicked.connect(self._remove_from_cart)
        cart_btn_row.addWidget(self.btn_remove_cart)

        self.btn_clear_cart = QPushButton("✕  Clear Selection")
        self.btn_clear_cart.setObjectName("btn_danger")
        self.btn_clear_cart.setEnabled(False)
        self.btn_clear_cart.clicked.connect(self._clear_cart)
        cart_btn_row.addWidget(self.btn_clear_cart)

        cart_btn_row.addStretch()

        self.btn_export_cart = QPushButton("💾  Export All Selected (One File)")
        self.btn_export_cart.setObjectName("btn_success")
        self.btn_export_cart.setEnabled(False)
        self.btn_export_cart.setToolTip(
            "احفظ كل الوحدات في السلة في ملف واحد.")
        self.btn_export_cart.clicked.connect(self._export_cart)
        cart_btn_row.addWidget(self.btn_export_cart)

        selected_lyt.addLayout(cart_btn_row)

        self.tab_selected = selected_tab
        self.tabs.addTab(selected_tab, "🛒  Selected (0)")

        # ── Keyboard shortcuts for Systems navigation ──
        # Scoped to the Systems table so they don't fire elsewhere.
        self._nav_shortcut_prev = QShortcut(
            QKeySequence("Ctrl+Up"), self.table_batch)
        self._nav_shortcut_prev.setContext(Qt.WidgetWithChildrenShortcut)
        self._nav_shortcut_prev.activated.connect(
            lambda: self._navigate_system(-1))

        self._nav_shortcut_next = QShortcut(
            QKeySequence("Ctrl+Down"), self.table_batch)
        self._nav_shortcut_next.setContext(Qt.WidgetWithChildrenShortcut)
        self._nav_shortcut_next.activated.connect(
            lambda: self._navigate_system(+1))

        # ── Inverter ROI tab ───────────────────────────────────
        if ROI_AVAILABLE:
            self.roi_calculator = InverterROICalculator()
            self.tabs.addTab(self.roi_calculator, "⚡  Inverter ROI")
        else:
            self.roi_calculator = None

        # ── Electrical Load tab ────────────────────────────────
        if ELEC_AVAILABLE:
            self.electrical_tab = ElectricalScheduleTab()
            self.tabs.addTab(self.electrical_tab, "🔌  Electrical")
        else:
            self.electrical_tab = None

        # ── OCR Debug tab ──────────────────────────────────────
        debug_tab = QWidget()
        debug_lyt = QVBoxLayout(debug_tab)
        debug_lyt.setContentsMargins(0, 4, 0, 0)
        lbl_debug = QLabel("Raw Tesseract output — useful for diagnosing extraction failures:")
        lbl_debug.setStyleSheet("color: #6b7280; font-size: 12px; padding: 2px 4px;")
        debug_lyt.addWidget(lbl_debug)
        self.txt_debug = QTextEdit()
        self.txt_debug.setReadOnly(True)
        self.txt_debug.setPlaceholderText("OCR text will appear here after uploading…")
        debug_lyt.addWidget(self.txt_debug)
        self.tab_debug = debug_tab
        self.tabs.addTab(debug_tab, "🔍  OCR Debug")

        layout.addWidget(self.tabs)
        return panel

    # ──────────────────────────────────────────────────────────────
    # OCR Workflow
    # ──────────────────────────────────────────────────────────────
    def upload_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select HAP Report",
            "",
            "HAP Reports (*.pdf *.PDF *.rtf *.RTF *.png *.PNG "
            "*.jpg *.JPG *.jpeg *.JPEG *.bmp *.BMP *.tiff *.TIFF);;"
            "RTF Report (*.rtf *.RTF);;"
            "PDF Files (*.pdf *.PDF);;"
            "Image Files (*.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
            "*.bmp *.BMP *.tiff *.TIFF);;"
            "All Files (*)"
        )
        if not path:
            return
        self._load_report(path)

    def _load_report(self, path: str):
        """Load a HAP report from `path` (dialog + drag-and-drop share this)."""
        # Remember uploaded file's base name (e.g. "G01" from "G01.png")
        # so exports default to G01.xlsx / G01_selected.xlsx / G01_multi.xlsx
        base = os.path.splitext(os.path.basename(path))[0].strip()
        self.source_basename = base
        self.current_system_name = ""  # reset old system name immediately

        # Reset batch state for the new report
        self.systems = []
        self._render_batch()

        # Preview
        if path.lower().endswith(".rtf"):
            self.lbl_image.setText("📄  RTF report loaded")
            self.lbl_image.setStyleSheet(
                "color: #34d399; border: 2px dashed #374151; "
                "border-radius: 6px; font-size: 14px;")
        elif path.lower().endswith(".pdf"):
            pix = self._pdf_first_page_pixmap(path)
            if pix.isNull():
                self.lbl_image.setText("PDF preview unavailable")
            else:
                self.lbl_image.setPixmap(
                    pix.scaled(self.lbl_image.size(),
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
        else:
            pix = QPixmap(path)
            self.lbl_image.setPixmap(
                pix.scaled(self.lbl_image.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

        # Kick off background OCR
        self.btn_upload.setEnabled(False)
        self.btn_upload.setText("⏳  Running OCR…")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self._set_status("Running OCR — please wait…")

        self.ocr_worker = OCRWorker(path)
        self.ocr_worker.progress.connect(self.progress_bar.setValue)
        self.ocr_worker.done.connect(self._on_ocr_done)
        self.ocr_worker.error.connect(self._on_ocr_error)
        self.ocr_worker.start()

    def _on_ocr_done(self, text: str):
        self.txt_debug.setPlainText(text)
        self.progress_bar.setValue(100)
        self.progress_bar.setVisible(False)
        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("📁  Upload HAP Report")

        res = core.parse_hap_text(text)
        btu, cfm = res.btu, res.cfm

        # Batch: detect every system in this report (may be just one)
        self.systems = core.parse_all_systems(text)
        self._render_batch()
        # Single-system report → remember its name for export defaults
        if len(self.systems) == 1:
            self.current_system_name = self.systems[0].name
        else:
            self.current_system_name = ""  # stay blank until user picks a system

        # Always reset state from THIS report (never keep a previous report's values)
        self.current_btu, self.current_cfm = btu, cfm
        self.current_sensible = res.sensible_btu
        self.input_btu.setText(f"{btu:,.0f}" if btu > 0 else "")
        self.input_cfm.setText(f"{cfm:,.0f}" if cfm > 0 else "")
        self.input_sensible.setText(
            f"{res.sensible_btu:,.0f}" if res.sensible_btu > 0 else "")

        amber = "color: #f59e0b; font-size: 11px; padding: 2px 0;"
        green = "color: #34d399; font-size: 11px; padding: 2px 0;"

        if btu == 0 and cfm == 0:
            self.lbl_btu.setText("Cooling Load:  ⚠  Not extracted")
            self.lbl_cfm.setText("Airflow:      ⚠  Not extracted")
            for lbl in (self.lbl_btu, self.lbl_cfm):
                lbl.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 12px;")
            self.lbl_ocr_status.setText(
                "⚠  OCR could not read the report. Enter values manually "
                "then click Confirm & Search.")
            self.lbl_ocr_status.setStyleSheet(amber)
            self._set_status("⚠  OCR failed — enter values manually and confirm.")
            self.tabs.setCurrentWidget(self.tab_debug)
            return

        self._update_extracted_labels()
        if res.warnings:
            self.lbl_ocr_status.setText("⚠  " + "  ".join(res.warnings)
                                        + "  Verify / complete the values, then Confirm.")
            self.lbl_ocr_status.setStyleSheet(amber)
            self._set_status("⚠  OCR partly read the report — please verify the values.")
        else:
            self.lbl_ocr_status.setText(
                f"✅  OCR extracted: {btu:,.0f} Btu/hr | {cfm:,.0f} CFM  —  "
                f"verify the values above, then click Confirm & Search.")
            self.lbl_ocr_status.setStyleSheet(green)
            self._set_status(
                f"✅  OCR complete — please verify: {btu:,.0f} Btu/hr | {cfm:,.0f} CFM")
        # If the report contained multiple systems, jump to the Systems tab
        if len(self.systems) > 1:
            self.tabs.setCurrentWidget(self.tab_systems)
        else:
            self.tabs.setCurrentWidget(self.tab_results)
        self._roi_update_capacity(btu, units=1)
        self._elec_sync_from_cart()

        self.input_btu.setFocus()
        self.input_btu.selectAll()

    def _on_ocr_error(self, msg: str):
        self.progress_bar.setVisible(False)
        self.btn_upload.setEnabled(True)
        self.btn_upload.setText("📁  Upload HAP Report")
        self._set_status(f"OCR error: {msg}")
        QMessageBox.critical(self, "OCR Error", msg)

    # ──────────────────────────────────────────────────────────────
    # PDF Helpers
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _pdf_first_page_pixmap(path):
        """Return a QPixmap of the first page of the PDF, or a null QPixmap."""
        if not PDF_AVAILABLE:
            return QPixmap()
        try:
            doc = fitz.open(path)
            try:
                if len(doc) == 0:
                    return QPixmap()
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                img = QImage(
                    pix.samples, pix.width, pix.height,
                    pix.stride, QImage.Format_RGB888
                )
                # .copy() detaches from the pymupdf buffer, which may be freed
                return QPixmap.fromImage(img.copy())
            finally:
                doc.close()
        except Exception:
            return QPixmap()

    # ──────────────────────────────────────────────────────────────
    # Filtering & rendering (logic lives in core.py)
    # ──────────────────────────────────────────────────────────────
    def _filter_args(self):
        return dict(
            safety_pct=self.spin_safety.value(),
            cfm_cover_pct=self.spin_cover.value(),
            derate_pct=self.spin_derate.value(),
            category=self.combo_category.currentText(),
            type_=self.combo_type.currentText(),
            power_supply=self.combo_power.currentText(),
            series=self.combo_series.currentText(),
        )

    def apply_filters(self):
        if self.current_btu == 0 and self.current_cfm == 0:
            return

        kw = self._filter_args()
        filtered = core.select_units(
            self.df, self.current_btu, self.current_cfm,
            sensible_btu=self.current_sensible, **kw)
        multi = core.multi_unit_options(
            self.df, self.current_btu, self.current_cfm, **kw)

        self.picked_df_index = None
        self.current_results = filtered
        self._render_table(filtered)
        self._render_multi_unit_table(multi)
        self._render_batch()

        target_btu = self.current_btu * (1 + kw["safety_pct"] / 100.0)
        need_cfm = self.current_cfm * kw["cfm_cover_pct"] / 100.0
        tons_str = f"({target_btu/core.BTU_PER_TON:.2f} Tons)" if target_btu > 0 else "(no load)"
        cfm_str = (f"≥ {need_cfm:,.0f} CFM" if need_cfm > 0
                   else "CFM not set — airflow filter skipped")
        self._set_status(
            f"{'✅' if len(filtered) > 0 else '⚠'} "
            f"{len(filtered)} single-unit match(es)  |  "
            f"Required: ≥ {target_btu:,.0f} Btu/hr {tons_str}  |  {cfm_str}"
        )
        self.btn_export.setEnabled(len(filtered) > 0)

    @staticmethod
    def _num_item(text, value=None, fg=None, bg=None):
        cell = NumItem(text, value)
        cell.setTextAlignment(Qt.AlignCenter)
        if bg is not None:
            cell.setBackground(QBrush(bg))
        if fg is not None:
            cell.setForeground(QBrush(fg))
        return cell

    @staticmethod
    def _txt(val):
        return '' if (val is None or (isinstance(val, float) and pd.isna(val))) else str(val)


    # ──────────────────────────────────────────────────────────────
    # Best-Unit picker
    # ──────────────────────────────────────────────────────────────
    def _best_overall_index(self, df):
        """Best-balanced unit: 0.40*size + 0.35*EER + 0.25*airflow (0..1)."""
        score = pd.Series(0.0, index=df.index)

        ex = df["Excess %"].clip(lower=0)
        if ex.max() > ex.min():
            score += 0.40 * (1 - (ex - ex.min()) / (ex.max() - ex.min()))
        else:
            score += 0.40

        eer = df["EER (Btu/Wh)"]
        eer = eer.fillna(eer.min() if eer.notna().any() else 0)
        if eer.max() > eer.min():
            score += 0.35 * (eer - eer.min()) / (eer.max() - eer.min())
        else:
            score += 0.35

        cov = df["CFM Coverage %"]
        if cov.notna().any():
            delta = (cov - 100).abs().fillna((cov - 100).abs().max())
            if delta.max() > delta.min():
                score += 0.25 * (1 - (delta - delta.min()) / (delta.max() - delta.min()))
            else:
                score += 0.25
        else:
            score += 0.25

        return score.idxmax()

    def _select_df_row(self, df_idx):
        """Select + scroll to the table row whose UserRole == df_idx."""
        self.picked_df_index = int(df_idx)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is None:
                continue
            if item.data(Qt.UserRole) == int(df_idx):
                self.table.selectRow(row)
                self.table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                return True
        return False

    def _pick_best(self, criterion: str):
        """Highlight the best-matching row according to the chosen criterion."""
        df = self.current_results
        if df is None or df.empty:
            QMessageBox.information(self, "No Results",
                                    "Run a search first (Upload + Confirm & Search).")
            return

        if criterion == "size":
            idx = df["Excess %"].idxmin()
            why = f"least oversized ({df.loc[idx, 'Excess %']:.1f}% excess)"
        elif criterion == "eer":
            valid = df[df["EER (Btu/Wh)"].notna()]
            if valid.empty:
                QMessageBox.information(self, "No EER data",
                                        "No EER values available.")
                return
            idx = valid["EER (Btu/Wh)"].idxmax()
            why = f"highest EER ({valid.loc[idx, 'EER (Btu/Wh)']:g})"
        elif criterion == "cfm":
            cov = df["CFM Coverage %"]
            valid = df[cov.notna() & (cov >= 100)]
            if valid.empty:
                valid = df[cov.notna()]
            if valid.empty:
                QMessageBox.information(self, "No CFM data",
                                        "No CFM coverage values available.")
                return
            idx = (valid["CFM Coverage %"] - 100).abs().idxmin()
            why = f"CFM coverage {valid.loc[idx, 'CFM Coverage %']:.0f}%"
        else:  # "overall"
            idx = self._best_overall_index(df)
            why = "balanced score (size + EER + airflow)"

        if not self._select_df_row(idx):
            self._set_status("⚠  Could not locate the row (try re-sorting).")
            return
        model = df.loc[idx, "System Model"]
        self._set_status(f"🏆  Best ({criterion}): {model} — {why}")

    def _export_picked(self):
        """Export the picked (or selected) row only."""
        df = self.current_results
        if df is None or df.empty:
            QMessageBox.warning(self, "No Data", "Run a search first.")
            return

        idx = self.picked_df_index
        if idx is None or idx not in df.index:
            rows = {item.row() for item in self.table.selectedItems()}
            if not rows:
                QMessageBox.information(
                    self, "Nothing Picked",
                    "Click one of the “Pick Best” buttons first, or select a row.")
                return
            idx = self.table.item(min(rows), 0).data(Qt.UserRole)

        try:
            subset = df.loc[[idx]]
        except KeyError:
            QMessageBox.warning(self, "Stale Selection",
                                "The table changed — pick again.")
            return

        model = str(subset.iloc[0]["System Model"])
        safe = re.sub(r"[^\w.\-]+", "_", model)

        path, selected_filter = self._save_file_dialog("Save Picked Unit",
            self._default_export_name("xlsx", suffix=f"_{safe}"),
            "Excel Workbook (*.xlsx);;"
            "Word Report (*.docx);;"
            "CSV File (*.csv)"
        )
        if not path:
            return
        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()
        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_word_report(subset, temporary_path,
                                            title_suffix=f"Picked unit ({model})")
                elif ext == ".csv":
                    self._prepare_export_df(subset).to_csv(
                        temporary_path, index=False, encoding="utf-8-sig")
                else:
                    self._prepare_export_df(subset).to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)
            self._set_status(f"✅  Picked unit exported → {path}")
            QMessageBox.information(self, "Export Successful",
                                    f"{model} saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _render_table(self, df: pd.DataFrame):
        """Populate the results table; first row (least oversized) highlighted."""
        columns = list(_RESULTS_TABLE_COLUMNS)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(df))
        self.table.setColumnCount(len(columns))
        self.table.setHorizontalHeaderLabels(columns)

        BEST_BG, BEST_FG, NORM_FG = QColor("#0d2e1c"), QColor("#34d399"), QColor("#e2e8f0")

        for row_i, (df_idx, r) in enumerate(df.iterrows()):
            is_best = (row_i == 0)
            fg, bg = (BEST_FG if is_best else NORM_FG), (BEST_BG if is_best else None)
            btu = float(r['Cooling Capacity (Btu/hr)'])
            cfm = float(r['High CFM (numeric)'])
            cov = r['CFM Coverage %']
            eer = r['EER (Btu/Wh)']
            cells = [
                (self._txt(r['Category']), None),
                (self._txt(r['Series']), None),
                (self._txt(r['System Model']), None),
                (self._txt(r['Indoor Unit Model']), None),
                (self._txt(r['Outdoor Unit Model']), None),
                (f"{btu:,.0f}", btu),
                (f"{btu/core.BTU_PER_TON:.2f}", btu),
                (f"{btu/core.BTU_PER_KW:.2f}", btu),
                (f"{r['Excess %']:.1f} %", float(r['Excess %'])),
                (f"{cfm:,.0f}" + (" ≈" if r['CFM Approx'] else ""), cfm),
                ("—" if pd.isna(cov) else f"{cov:.0f} %", None if pd.isna(cov) else float(cov)),
                (self._txt(r['Sensible OK']), None,
                 QColor("#f87171") if r['Sensible OK'] == '✘' else (QColor("#34d399") if r['Sensible OK'] == '✔' else None),
                 QColor("#450a0a") if r['Sensible OK'] == '✘' else None),
                ("" if pd.isna(eer) else f"{eer:g}", None if pd.isna(eer) else float(eer)),
                (self._txt(r['Type']), None),
                (self._txt(r['Power Supply']), None),
            ]
            cells = _reorder_results_table_cells(cells)
            for col_i, spec in enumerate(cells):
                text, val, *extra = spec
                cell_fg = extra[0] if len(extra) > 0 and extra[0] is not None else fg
                cell_bg = extra[1] if len(extra) > 1 and extra[1] is not None else bg
                cell = self._num_item(text, val, cell_fg, cell_bg)
                cell.setData(Qt.UserRole, int(df_idx))   # remember DataFrame index
                self.table.setItem(row_i, col_i, cell)

        self.table.setSortingEnabled(True)
        # Calculate initial widths once; the header remains Interactive so users
        # can still drag column boundaries afterward.
        self.table.resizeColumnsToContents()

        if len(df) > 0:
            self.lbl_count.setText(
                f"✅  {len(df)} unit(s) matched  (green = least oversized — "
                f"double-click to copy model;  ≈ = approximate catalogue CFM)")
            self.lbl_count.setStyleSheet("color: #34d399; font-size: 12px; padding: 2px 4px;")
        else:
            self.lbl_count.setText(
                "⚠  No single unit covers this load. Relax the filters "
                "(Min CFM / Safety / Derating) or see the Multi-Unit tab.")
            self.lbl_count.setStyleSheet("color: #f87171; font-size: 12px; padding: 2px 4px;")

    def _render_multi_unit_table(self, multi: pd.DataFrame):
        """Identical-unit combinations (≤ core.MAX_MULTI_UNITS), best first."""
        cols = ['# Units', 'Category', 'Series', 'System Model',
                'Single-Unit BTU/hr', 'Total BTU/hr', 'Excess %',
                'Single-Unit CFM', 'Total CFM', 'EER', 'Type']
        self.table_multi.setSortingEnabled(False)
        self.table_multi.setColumnCount(len(cols))
        self.table_multi.setHorizontalHeaderLabels(cols)

        if multi is None or multi.empty:
            self.table_multi.setRowCount(0)
            self.lbl_multi_hint.setText(
                f"No combination of up to {core.MAX_MULTI_UNITS} identical units "
                f"covers the requirement with the current filters.")
            self.table_multi.setSortingEnabled(True)
            return

        self.table_multi.setRowCount(len(multi))
        BEST_BG, BEST_FG, NORM_FG = QColor("#0d2e1c"), QColor("#34d399"), QColor("#e2e8f0")
        for r_i, r in multi.iterrows():
            fg, bg = (BEST_FG if r_i == 0 else NORM_FG), (BEST_BG if r_i == 0 else None)
            eer = r['EER']
            cells = [
                (str(int(r['Units'])), float(r['Units'])),
                (self._txt(r['Category']), None), (self._txt(r['Series']), None),
                (self._txt(r['System Model']), None),
                (f"{r['Unit Btu']:,.0f}", float(r['Unit Btu'])),
                (f"{r['Total Btu']:,.0f}", float(r['Total Btu'])),
                (f"{r['Excess %']:.1f} %", float(r['Excess %'])),
                (f"{r['Unit CFM']:,.0f}", float(r['Unit CFM'])),
                (f"{r['Total CFM']:,.0f}", float(r['Total CFM'])),
                ("" if pd.isna(eer) else f"{eer:g}", None if pd.isna(eer) else float(eer)),
                (self._txt(r['Type']), None),
            ]
            for c_i, (text, val) in enumerate(cells):
                self.table_multi.setItem(r_i, c_i, self._num_item(text, val, fg, bg))
        self.table_multi.setSortingEnabled(True)
        self.lbl_multi_hint.setText(
            f"Minimum combination: {int(multi.iloc[0]['Units'])} identical unit(s) "
            f"(max {core.MAX_MULTI_UNITS}).  {len(multi)} option(s), best highlighted.")

    def _batch_cache_key_for(self):
        """Signature that invalidates the batch cache on systems/filter change."""
        return (
            tuple((s.name, s.btu, s.cfm, s.sensible_btu) for s in self.systems),
            self.spin_safety.value(),
            self.spin_cover.value(),
            self.spin_derate.value(),
            self.combo_category.currentText(),
            self.combo_type.currentText(),
            self.combo_series.currentText(),
            self.combo_power.currentText(),
        )

    def _render_batch(self):
        """Wrapper around _render_batch_body with a re-entrancy guard.

        Rebuilding the table (row count, item inserts, re-sorting) fires
        itemSelectionChanged, which would otherwise yank the user to the
        Results tab whenever a filter spinbox changed.
        """
        if not hasattr(self, "table_batch") or self.table_batch is None:
            return
        self._updating_batch = True
        try:
            self._render_batch_body()
        finally:
            self._updating_batch = False

    def _render_batch_body(self):
        """One row per detected system: load + best matching unit."""
        if not hasattr(self, "table_batch") or self.table_batch is None:
            return

        cols = ['System', 'Load (Btu/hr)', 'Tons', 'CFM', 'Sensible (MBH)',
                'Status', 'Units', 'System Model', 'Indoor Unit',
                'Outdoor Unit', 'Unit Btu', 'Excess %', 'CFM Cover %',
                'Sensible OK', 'EER', 'Notes']
        self.table_batch.setSortingEnabled(False)
        self.table_batch.setColumnCount(len(cols))
        self.table_batch.setHorizontalHeaderLabels(cols)

        if not self.systems:
            self.table_batch.setRowCount(0)
            self.batch_results = None
            self._batch_cache_key = None
            self.lbl_batch_hint.setText(
                "Upload a full HAP Systems Design Report — every air system "
                "is detected automatically and the best unit is picked for each.")
            self.table_batch.setSortingEnabled(True)
            return

        # Cache: only re-run batch_select when systems or filters changed.
        key = self._batch_cache_key_for()
        if key == self._batch_cache_key and self.batch_results is not None:
            batch = self.batch_results
        else:
            batch = core.batch_select(self.df, self.systems,
                                      **self._filter_args())
            self._batch_cache_key = key
        self.batch_results = batch
        self.table_batch.setRowCount(len(batch))

        for r_i, (_, r) in enumerate(batch.iterrows()):
            no_match = r['Status'] in ('No match', 'Not parsed')
            fg = QColor("#f87171") if no_match else QColor("#e2e8f0")
            sens = r['Sensible (Btu/hr)']
            cells = [
                (str(r['System']), None),
                (f"{r['Load (Btu/hr)']:,.0f}", float(r['Load (Btu/hr)'])),
                (f"{r['Load (Tons)']:.2f}", float(r['Load (Tons)'])),
                (f"{r['Required CFM']:,.0f}", float(r['Required CFM'])),
                ("—" if not sens else f"{sens/1000:.1f}", None),
                (str(r['Status']), None),
                (str(int(r['Units'])), float(r['Units'])),
                (str(r['System Model'] or '—'), None),
                (str(r['Indoor Unit'] or '—'), None),
                (str(r['Outdoor Unit'] or '—'), None),
                ("—" if pd.isna(r['Unit Btu']) else f"{r['Unit Btu']:,.0f}",
                 None if pd.isna(r['Unit Btu']) else float(r['Unit Btu'])),
                ("—" if pd.isna(r['Excess %']) else f"{r['Excess %']:.1f} %",
                 None if pd.isna(r['Excess %']) else float(r['Excess %'])),
                ("—" if pd.isna(r['CFM Cover %']) else f"{r['CFM Cover %']:.0f} %",
                 None if pd.isna(r['CFM Cover %']) else float(r['CFM Cover %'])),
                (str(r['Sensible OK']), None),
                ("—" if pd.isna(r['EER']) else f"{r['EER']:g}",
                 None if pd.isna(r['EER']) else float(r['EER'])),
                (str(r['Notes'] or ''), None),
            ]
            for c_i, (text, val) in enumerate(cells):
                self.table_batch.setItem(r_i, c_i, self._num_item(text, val, fg))

        self.table_batch.setSortingEnabled(True)
        n_ok = int((~batch['Status'].isin(['No match', 'Not parsed'])).sum())
        self.lbl_batch_hint.setText(
            f"🏢 {len(batch)} system(s) detected — {n_ok} matched, "
            f"{len(batch) - n_ok} need attention.  "
            f"👉  Click a row to jump to Results, or use Prev/Next to browse.")
        self._update_nav_position()

    def _navigate_system(self, delta: int):
        """Move the Systems-tab selection by `delta` rows (wraps around).

        Uses _nav_silent so _on_batch_row_selected still updates the Verify
        Values, but does NOT jump to the Results tab — this lets the user
        browse 20+ systems quickly without losing their place.
        """
        n = self.table_batch.rowCount()
        if n == 0:
            return
        row = self.table_batch.currentRow()
        if row < 0:
            row = 0 if delta > 0 else n - 1
        else:
            row = (row + delta) % n
        self._nav_silent = True
        try:
            self.table_batch.setCurrentCell(row, 0)
            item = self.table_batch.item(row, 0)
            if item is not None:
                self.table_batch.scrollToItem(item)
        finally:
            self._nav_silent = False
        self._update_nav_position()

    def _update_nav_position(self):
        """Refresh the "N / M" label next to the Prev/Next buttons."""
        if not hasattr(self, "lbl_nav_pos"):
            return
        n = self.table_batch.rowCount()
        if n == 0:
            self.lbl_nav_pos.setText("—")
            return
        row = self.table_batch.currentRow()
        if row < 0:
            self.lbl_nav_pos.setText(f"— / {n}")
        else:
            self.lbl_nav_pos.setText(f"{row + 1} / {n}")

    def _view_results_from_systems(self):
        """Explicitly jump to Results for the currently selected system."""
        if self.table_batch.currentRow() < 0:
            QMessageBox.information(
                self, "No System Selected",
                "Click a system row (or use Prev/Next) first.")
            return
        # Re-run the handler in non-silent mode to force the tab switch.
        self._nav_silent = False
        self._on_batch_row_selected()

    def _on_batch_row_selected(self):
        """Load the clicked system's load into Verify Values and jump to Results.

        Runs the same select_units / multi_unit_options pipeline that
        Confirm & Search runs, but skips re-rendering the Systems table
        (that would clear the selection and cause churn).

        Guarded by _updating_batch: while _render_batch is rebuilding the
        table, selection-changed events are ignored so a filter tweak
        doesn't yank the user to the Results tab.
        """
        if getattr(self, "_updating_batch", False):
            return
        items = self.table_batch.selectedItems()
        if not items:
            return
        row = items[0].row()
        name_item = self.table_batch.item(row, 0)
        if name_item is None:
            return
        name = name_item.text().strip()

        s = next((x for x in self.systems if x.name == name), None)
        if s is None:
            return
        self.current_system_name = name   # drives export filename / content

        # Nothing changed? Don't re-run the pipeline.
        if (abs(s.btu - self.current_btu) < self._EPS_BTU
                and abs(s.cfm - self.current_cfm) < self._EPS_CFM):
            self._update_nav_position()
            if not getattr(self, "_nav_silent", False):
                self.tabs.setCurrentWidget(self.tab_results)
            return

        self.current_btu = s.btu
        self.current_cfm = s.cfm
        self.current_sensible = s.sensible_btu
        self.input_btu.setText(f"{s.btu:,.0f}" if s.btu > 0 else "")
        self.input_cfm.setText(f"{s.cfm:,.0f}" if s.cfm > 0 else "")
        self.input_sensible.setText(
            f"{s.sensible_btu:,.0f}" if s.sensible_btu > 0 else "")
        self._update_extracted_labels()
        self._roi_update_capacity(s.btu, units=1)
        self._elec_sync_from_cart()

        kw = self._filter_args()
        filtered = core.select_units(self.df, s.btu, s.cfm,
                                     sensible_btu=s.sensible_btu, **kw)
        multi = core.multi_unit_options(self.df, s.btu, s.cfm, **kw)
        self.current_results = filtered
        self._render_table(filtered)
        self._render_multi_unit_table(multi)
        self.btn_export.setEnabled(len(filtered) > 0)

        self._update_nav_position()
        if not getattr(self, "_nav_silent", False):
            self.tabs.setCurrentWidget(self.tab_results)
        self._set_status(
            f"📌 {name}: {s.btu:,.0f} Btu/hr | {s.cfm:,.0f} CFM  —  "
            f"{len(filtered)} single-unit match(es)."
            + ("   (Press Ctrl+Down for next, or click View Results)"
               if getattr(self, "_nav_silent", False) else ""))

    def _export_batch(self):
        if self.batch_results is None or self.batch_results.empty:
            QMessageBox.warning(self, "No Data",
                                "No systems detected yet — upload a report first.")
            return
        path, selected_filter = self._save_file_dialog("Save Systems Summary",
            self._default_export_name("xlsx", suffix="_systems"),
            "Excel Workbook (*.xlsx);;"
            "Word Report (*.docx);;"
            "CSV File (*.csv)")
        if not path:
            return
        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()
        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_batch_word_report(
                        self.batch_results, temporary_path,
                        title_suffix=f"Systems summary ({len(self.batch_results)})")
                else:
                    out = self.batch_results.copy()
                    for col in ("Load (Btu/hr)", "Sensible (Btu/hr)", "Required CFM",
                                "Unit Btu", "Total Btu", "Unit CFM", "Total CFM"):
                        if col in out.columns:
                            out[col] = out[col].round(0)
                    for col in ("Excess %", "CFM Cover %", "Load (Tons)"):
                        if col in out.columns:
                            out[col] = out[col].round(1)
                    out = out.fillna("")
                    if ext == ".csv":
                        out.to_csv(temporary_path, index=False, encoding="utf-8-sig")
                    else:
                        out.to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)
            self._set_status(f"✅  Systems summary exported → {path}")
            QMessageBox.information(self, "Export Successful",
                                    f"File saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _build_batch_word_report(self, batch, path, title_suffix=""):
        """Word report: one row per detected system + best matching unit."""
        if not DOCX_AVAILABLE:
            raise RuntimeError(
                "python-docx is not installed. Run:\n"
                "  python -m pip install python-docx"
            )
        from datetime import datetime

        doc = Document()
        for section in doc.sections:
            section.left_margin = Inches(0.7)
            section.right_margin = Inches(0.7)
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)

        h = doc.add_heading("HAP Systems Design Report — Summary", level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if title_suffix:
            sub = doc.add_paragraph(title_suffix)
            sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub.runs[0].italic = True

        date_p = doc.add_paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in date_p.runs:
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        cols = ["System", "Load (Btu/hr)", "Tons", "CFM",
                "Status", "Units", "System Model", "Indoor Unit",
                "Outdoor Unit", "Excess %", "EER"]
        tbl = doc.add_table(rows=1, cols=len(cols))
        tbl.style = "Light Grid Accent 1"
        hdr = tbl.rows[0].cells
        for i, name in enumerate(cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(8)

        for _, row in batch.iterrows():
            cells = tbl.add_row().cells
            cells[0].text = str(row.get('System', ''))
            cells[1].text = f"{row.get('Load (Btu/hr)', 0):,.0f}"
            cells[2].text = f"{row.get('Load (Tons)', 0):.2f}"
            cells[3].text = f"{row.get('Required CFM', 0):,.0f}"
            cells[4].text = str(row.get('Status', ''))
            cells[5].text = str(int(row.get('Units', 0)))
            cells[6].text = str(row.get('System Model', '') or '—')
            cells[7].text = str(row.get('Indoor Unit', '') or '—')
            cells[8].text = str(row.get('Outdoor Unit', '') or '—')
            ex = row.get('Excess %')
            cells[9].text = "—" if pd.isna(ex) else f"{ex:.1f}%"
            eer = row.get('EER')
            cells[10].text = "—" if pd.isna(eer) else f"{eer:g}"
            for c in cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(8)

        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            "Generated by Carrier AC Selector — HAP Report Analyzer"
        )
        run.italic = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

        self._roi_append_to_docx(doc)
        self._elec_append_to_docx(doc)

        doc.save(path)

    # ──────────────────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────────────────
    def confirm_and_search(self):
        """Read the (possibly edited) values from the input fields and filter."""
        btu_str = self.input_btu.text().strip()
        cfm_str = self.input_cfm.text().strip()
        sens_str = self.input_sensible.text().strip()

        if not btu_str and not cfm_str:
            QMessageBox.warning(
                self, "No Values",
                "Enter at least a BTU/hr or a CFM value, then click Confirm & Search."
            )
            return

        new_btu = core.parse_user_number(btu_str)
        new_cfm = core.parse_user_number(cfm_str)

        if new_btu is None or new_cfm is None:
            QMessageBox.warning(
                self, "Invalid Input",
                "Please enter valid, positive numbers (e.g. 82800 or 12.5)."
            )
            return

        try:
            new_sens = float(sens_str) if sens_str else 0.0
            if not math.isfinite(new_sens) or new_sens < 0:
                new_sens = 0.0
        except ValueError:
            new_sens = core.parse_user_number(sens_str) or 0.0

        changed = (
            abs(new_btu - self.current_btu) > self._EPS_BTU or
            abs(new_cfm - self.current_cfm) > self._EPS_CFM
        )

        # Warn if user supplied CFM only (results won't be capacity-filtered)
        if new_btu == 0 and new_cfm > 0:
            reply = QMessageBox.question(
                self, "No Cooling Load",
                "You entered only CFM — the results won't be filtered by capacity.\n"
                "Continue anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return

        self.current_btu = new_btu
        self.current_cfm = new_cfm
        self.current_sensible = new_sens
        self._update_extracted_labels()
        self._roi_update_capacity(new_btu, units=1)
        self._elec_sync_from_cart()
        self.apply_filters()
        if new_btu > 0 and new_cfm == 0:
            self._set_status("⚠  CFM is empty — results are filtered by capacity only.")
            return

        if changed:
            self.lbl_ocr_status.setText(
                "✏  Values edited manually — results refreshed."
            )
            self.lbl_ocr_status.setStyleSheet(
                "color: #60a5fa; font-size: 11px; padding: 2px 0;"
            )
            self._set_status("Values updated manually — results refreshed.")
        else:
            self._set_status("✅  Values confirmed — results shown.")

    # Backwards-compatible alias
    def apply_manual(self):
        self.confirm_and_search()

    @staticmethod
    def _to_float(value):
        """Float or None (handles '—', '≈10.5', blanks)."""
        return core.to_float(value)

    @staticmethod
    def _fmt_num(value):
        v = core.to_float(value)
        return "" if v is None else f"{v:g}"

    def _build_word_report(self, df, path, title_suffix=""):
        """Write a formatted Word report describing the selected units."""
        if not DOCX_AVAILABLE:
            raise RuntimeError(
                "python-docx is not installed. Run:\n"
                "  python -m pip install python-docx"
            )

        doc = Document()

        # Page margins
        for section in doc.sections:
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)

        # ── Title ──
        h = doc.add_heading("Carrier AC Selection Report", level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

        if self.current_system_name:
            title_suffix = (f"Air System: {self.current_system_name}"
                            + (f"  •  {title_suffix}" if title_suffix else ""))
        if title_suffix:
            sub = doc.add_paragraph(title_suffix)
            sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub.runs[0].italic = True

        from datetime import datetime
        date_p = doc.add_paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in date_p.runs:
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        # ── Project requirements ──
        doc.add_heading("Project Requirements", level=1)

        req_tbl = doc.add_table(rows=0, cols=2)
        req_tbl.style = "Light Grid Accent 1"

        def _add_req(label, value):
            row = req_tbl.add_row().cells
            row[0].text = label
            row[1].text = value
            for p in row[0].paragraphs:
                for r in p.runs:
                    r.bold = True

        btu = self.current_btu
        cfm = self.current_cfm
        sf = self.spin_safety.value()
        target_btu = btu * (1.0 + sf / 100.0)
        target_cfm = cfm

        if btu > 0:
            _add_req("Cooling Load (required)",
                     f"{target_btu:,.0f} Btu/hr   "
                     f"({target_btu/core.BTU_PER_TON:.2f} Tons | {target_btu/core.BTU_PER_KW:.2f} kW)")
        if cfm > 0:
            _add_req("Airflow (required)", f"{target_cfm:,.0f} CFM")
        if sf > 0:
            _add_req("Safety Factor", f"{sf}%")
        if self.current_system_name:
            _add_req("Air System", self.current_system_name)
        _add_req("Number of Units Selected", str(len(df)))

        doc.add_paragraph()

        # ── Units summary table ──
        doc.add_heading("Selected Units", level=1)

        summary_cols = [
            "#", "Category", "Series", "System Model",
            "Cooling (Btu/hr)", "Airflow (CFM)", "EER", "Type",
        ]
        stbl = doc.add_table(rows=1, cols=len(summary_cols))
        stbl.style = "Light Grid Accent 1"
        hdr = stbl.rows[0].cells
        for i, name in enumerate(summary_cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(9)

        for idx, (_, row) in enumerate(df.iterrows(), start=1):
            cells = stbl.add_row().cells
            cells[0].text = str(idx)
            cells[1].text = str(row.get('Category', ''))
            cells[2].text = str(row.get('Series', ''))
            cells[3].text = str(row.get('System Model', ''))
            btu_v = self._to_float(row.get('Cooling Capacity (Btu/hr)', 0)) or 0.0
            cells[4].text = f"{btu_v:,.0f}" if btu_v else "—"
            cells[5].text = self._fmt_num(row.get('High CFM (numeric)'))
            eer_v = self._to_float(row.get('EER (Btu/Wh)', None))
            cells[6].text = f"{eer_v:g}" if eer_v is not None else "—"
            cells[7].text = str(row.get('Type', ''))
            for c in cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)

        doc.add_paragraph()

        # ── Detailed spec sheet per unit ──
        doc.add_heading("Unit Specifications", level=1)

        for idx, (_, row) in enumerate(df.iterrows(), start=1):
            model = row.get('System Model', 'N/A')
            heading = doc.add_heading(f"{idx}. {model}", level=2)

            spec_tbl = doc.add_table(rows=0, cols=2)
            spec_tbl.style = "Light List Accent 1"

            def add_spec(label, value):
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    return
                s = str(value).strip()
                if s == "" or s.lower() == "nan":
                    return
                cells = spec_tbl.add_row().cells
                cells[0].text = label
                cells[1].text = s
                for p in cells[0].paragraphs:
                    for r in p.runs:
                        r.bold = True
                        r.font.size = Pt(10)
                for p in cells[1].paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(10)

            add_spec("Category",        row.get('Category', ''))
            add_spec("Series",          row.get('Series', ''))
            add_spec("System Model",    row.get('System Model', ''))
            add_spec("Indoor Unit",     row.get('Indoor Unit Model', ''))
            add_spec("Outdoor Unit",    row.get('Outdoor Unit Model', ''))
            add_spec("Type",            row.get('Type', ''))

            btu_v = self._to_float(row.get('Cooling Capacity (Btu/hr)', 0)) or 0.0
            if btu_v > 0:
                kw_v = btu_v / core.BTU_PER_KW
                add_spec("Cooling Capacity",
                         f"{btu_v:,.0f} Btu/hr  "
                         f"({btu_v/core.BTU_PER_TON:.2f} Tons | {kw_v:.2f} kW)")

            h_btu = self._to_float(row.get('Heating Capacity (Btu/hr)', None))
            if h_btu is not None and h_btu > 0:
                add_spec("Heating Capacity", f"{h_btu:,.0f} Btu/hr")

            add_spec("Airflow (CFM) L/M/H", row.get('Airflow (CFM) L/M/H', ''))
            add_spec("Airflow (m3/h)",      row.get('Airflow (m3/h)', ''))
            add_spec("High CFM (numeric)",  self._fmt_num(row.get('High CFM (numeric)')))
            add_spec("EER (Btu/Wh)",        row.get('EER (Btu/Wh)', ''))
            add_spec("Power Supply",        row.get('Power Supply', ''))
            add_spec("Indoor Unit Dimensions", row.get('Indoor Unit Dimensions (mm)', ''))
            add_spec("Indoor Unit Weight",  row.get('Indoor Unit Weight (kg)', ''))
            add_spec("Outdoor Unit Dimensions", row.get('Outdoor Unit Dimensions (mm)', ''))
            add_spec("Outdoor Unit Weight", row.get('Outdoor Unit Weight (kg)', ''))
            add_spec("Outdoor Sound Level", row.get('Outdoor Sound dB(A)', ''))
            add_spec("Approx. Area (m2)",   self._fmt_num(row.get('Approx. Area (m2)*')))

            notes = row.get('Notes', '')
            if notes and not (isinstance(notes, float) and pd.isna(notes)):
                add_spec("Notes", str(notes))

            doc.add_paragraph()

        # ── Footer note ──
        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            "Generated by Carrier AC Selector — HAP Report Analyzer"
        )
        run.italic = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

        self._roi_append_to_docx(doc)
        self._elec_append_to_docx(doc)

        doc.save(path)

    def _normalize_save_path(self, path, selected_filter):
        """Force the path's extension to match the selected file filter.

        Examples (uploaded G01.png -> source_basename = "G01"):
            "G01"      + Word filter    -> "G01.docx"
            "G01.docs" + Word filter    -> "G01.docx"
            "G01.xlsx" + Word filter    -> "G01.docx"
            "G01"      + CSV filter     -> "G01.csv"
            "G01.xlsx" + Excel filter   -> "G01.xlsx"  (unchanged)
        """
        m = re.search(r'\(\*\.(\w+)\)', selected_filter)
        if not m:
            return path
        required_ext = m.group(1)
        return _replace_export_extension(path, required_ext)

    def _default_export_name(self, ext="xlsx", suffix=""):
        """Default filename for export.

        Priority:
          1. the currently-active HAP system name (G01, AHU-1, …)
          2. the uploaded file's base name
          3. "Carrier_AC_Selection" as a last resort

        Example (upload "Systems Design Report.RTF", click G01 row):
            → "G01_selected.xlsx" / "G01.docx" / "G01.csv"
        """
        raw = (self.current_system_name
               or self.source_basename
               or "Carrier_AC_Selection")
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).strip() or "export"
        return f"{base}{suffix}.{ext}"

    def _save_file_dialog(self, title, default_path, filters_str):
        """Save dialog whose filename extension follows the selected filter.

        Drop-in replacement for QFileDialog.getSaveFileName that keeps the
        filename in sync with the file-type dropdown (Qt doesn't do that by
        default — you'd pick "Word Report" and still see "*.xlsx" in the box).

        Returns (path, selected_filter); path is "" if cancelled.
        """
        dlg = QFileDialog(self, title, default_path)
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setNameFilters(filters_str.split(";;"))
        dlg.setDefaultSuffix("xlsx")

        def _sync(name: str):
            m = re.search(r'\(\*\.(\w+)\)', name)
            if not m:
                return
            ext = m.group(1)
            dlg.setDefaultSuffix(ext)
            files = dlg.selectedFiles()
            cur = files[0] if files else default_path
            dlg.selectFile(_replace_export_extension(cur, ext))

        dlg.filterSelected.connect(_sync)
        if dlg.exec() != QFileDialog.Accepted:
            return "", ""
        return dlg.selectedFiles()[0], dlg.selectedNameFilter()

    def _prepare_export_df(self, df):
        """Copy of df for export: helper columns rounded, NaN -> '', reordered."""
        out = df.copy()
        # Prepend the active HAP system name so the file is self-describing
        if self.current_system_name:
            out.insert(0, "System", self.current_system_name)
        for col, nd in (("Excess %", 1), ("CFM Coverage %", 0), ("Effective Btu", 0)):
            if col in out.columns:
                out[col] = out[col].round(nd)

        preferred = [
            "System",
            "Category", "Series", "System Model",
            "Indoor Unit Model", "Outdoor Unit Model", "Type",
            "Cooling Capacity (Btu/hr)", "Effective Btu", "Excess %",
            "Airflow (CFM) L/M/H", "High CFM (numeric)",
            "CFM Coverage %", "CFM Approx", "Sensible OK",
            "EER (Btu/Wh)", "Power Supply",
            "Cooling Capacity (kW)", "Heating Capacity (Btu/hr)",
            "Heating Capacity (kW)", "Airflow (m3/h)",
            "Indoor Unit Dimensions (mm)", "Indoor Unit Weight (kg)",
            "Outdoor Unit Dimensions (mm)", "Outdoor Unit Weight (kg)",
            "Outdoor Sound dB(A)",
            "Approx. Area (m2)*", "Notes",
        ]
        ordered = [c for c in preferred if c in out.columns and c != "CFM Approx"]
        rest = [c for c in out.columns if c not in ordered and c != "CFM Approx"]
        res_df = out[ordered + rest].fillna("")
        for col in res_df.columns:
            res_df[col] = res_df[col].apply(
                lambda v: re.sub(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]|✔|✘', '', str(v)).strip()
                if isinstance(v, str) else v
            )
        return res_df

    def export_results(self):
        if self.current_results is None or len(self.current_results) == 0:
            QMessageBox.warning(self, "No Data", "Nothing to export.")
            return

        path, selected_filter = self._save_file_dialog("Save Results",
            self._default_export_name("xlsx"),
            "Excel Workbook (*.xlsx);;"
            "Word Report (*.docx);;"
            "CSV File (*.csv)"
        )
        if not path:
            return

        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()

        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_word_report(
                        self.current_results, temporary_path,
                        title_suffix=f"All matches ({len(self.current_results)} unit(s))",
                    )
                elif ext == ".csv":
                    export_df = self._prepare_export_df(self.current_results)
                    export_df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
                else:
                    export_df = self._prepare_export_df(self.current_results)
                    export_df.to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)

            self._set_status(f"Results exported → {path}")
            QMessageBox.information(
                self, "Export Successful", f"File saved to:\n{path}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _on_selection_changed(self):
        """Update UI when the user selects/deselects rows in the results table."""
        rows = {item.row() for item in self.table.selectedItems()}
        n = len(rows)
        self.btn_export_sel.setEnabled(n > 0)
        if n == 1:
            item0 = self.table.item(min(rows), 0)
            if item0 is not None:
                self.picked_df_index = item0.data(Qt.UserRole)
        if n > 0:
            self._set_status(
                f"🎯  {n} row(s) selected — click “Export Selected” to save them."
            )

    def export_selected(self):
        """Export only the rows the user has highlighted in the results table."""
        selected_items = self.table.selectedItems()
        if not selected_items:
            QMessageBox.warning(
                self, "No Selection",
                "Select one or more rows first (Ctrl+Click or Shift+Click)."
            )
            return
        if self.current_results is None or len(self.current_results) == 0:
            QMessageBox.warning(self, "No Data", "Nothing to export.")
            return

        df_indices = sorted({
            item.data(Qt.UserRole) for item in selected_items
            if item.data(Qt.UserRole) is not None
        })
        if not df_indices:
            QMessageBox.warning(
                self, "No Selection",
                "Could not map the selection back to the data. Try re-running the search."
            )
            return

        try:
            subset = self.current_results.loc[df_indices]
        except KeyError:
            QMessageBox.warning(
                self, "Stale Selection",
                "The table has changed since you selected. Please select again."
            )
            return

        path, selected_filter = self._save_file_dialog("Save Selected Units",
            self._default_export_name("xlsx", suffix="_selected"),
            "Excel Workbook (*.xlsx);;"
            "Word Report (*.docx);;"
            "CSV File (*.csv)"
        )
        if not path:
            return

        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()

        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_word_report(
                        subset, temporary_path,
                        title_suffix=f"Selected units ({len(subset)})",
                    )
                elif ext == ".csv":
                    export_df = self._prepare_export_df(subset)
                    export_df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
                else:
                    export_df = self._prepare_export_df(subset)
                    export_df.to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)

            self._set_status(
                f"✅  Exported {len(subset)} selected unit(s) → {path}"
            )
            QMessageBox.information(
                self, "Export Successful",
                f"{len(subset)} selected unit(s) saved to:\n{path}"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _export_multi(self):
        """Export the multi-unit options table to Excel / Word / CSV."""
        multi = core.multi_unit_options(
            self.df, self.current_btu, self.current_cfm, **self._filter_args())
        if multi is None or multi.empty:
            QMessageBox.warning(self, "No Data",
                                "No multi-unit combinations to export.")
            return

        path, selected_filter = self._save_file_dialog("Save Multi-Unit Options",
            self._default_export_name("xlsx", suffix="_multi"),
            "Excel Workbook (*.xlsx);;"
            "Word Report (*.docx);;"
            "CSV File (*.csv)"
        )
        if not path:
            return

        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()

        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_multi_word_report(
                        multi, temporary_path,
                        title_suffix=f"Multi-unit options ({len(multi)})")
                else:
                    out = multi.round({"Excess %": 1, "Unit Btu": 0, "Total Btu": 0,
                                       "Unit CFM": 0, "Total CFM": 0}).fillna("")
                    # Prepend the active HAP system name so the file is self-describing
                    if self.current_system_name:
                        out.insert(0, "System", self.current_system_name)
                    if ext == ".csv":
                        out.to_csv(temporary_path, index=False, encoding="utf-8-sig")
                    else:
                        out.to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)
            self._set_status(f"✅  Multi-unit exported → {path}")
            QMessageBox.information(self, "Export Successful",
                                    f"File saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _build_multi_word_report(self, multi, path, title_suffix=""):
        """Write a formatted Word report for multi-unit combinations."""
        if not DOCX_AVAILABLE:
            raise RuntimeError(
                "python-docx is not installed. Run:\n"
                "  python -m pip install python-docx"
            )
        from datetime import datetime

        doc = Document()
        for section in doc.sections:
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)
            section.top_margin = Inches(0.8)
            section.bottom_margin = Inches(0.8)

        h = doc.add_heading("Carrier AC Multi-Unit Selection Report", level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER

        if self.current_system_name:
            title_suffix = (f"Air System: {self.current_system_name}"
                            + (f"  •  {title_suffix}" if title_suffix else ""))
        if title_suffix:
            sub = doc.add_paragraph(title_suffix)
            sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub.runs[0].italic = True

        date_p = doc.add_paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in date_p.runs:
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        doc.add_heading("Project Requirements", level=1)
        req_tbl = doc.add_table(rows=0, cols=2)
        req_tbl.style = "Light Grid Accent 1"

        def _add_req(label, value):
            row = req_tbl.add_row().cells
            row[0].text = label
            row[1].text = value
            for p in row[0].paragraphs:
                for r in p.runs:
                    r.bold = True

        btu = self.current_btu
        cfm = self.current_cfm
        sf = self.spin_safety.value()
        target_btu = btu * (1.0 + sf / 100.0)

        if self.current_system_name:
            _add_req("Air System", self.current_system_name)
        if btu > 0:
            _add_req("Cooling Load (required)",
                     f"{target_btu:,.0f} Btu/hr   "
                     f"({target_btu/core.BTU_PER_TON:.2f} Tons | {target_btu/core.BTU_PER_KW:.2f} kW)")
        if cfm > 0:
            _add_req("Airflow (required)", f"{cfm:,.0f} CFM")
        if sf > 0:
            _add_req("Safety Factor", f"{sf}%")
        if self.spin_derate.value() > 0:
            _add_req("Derating", f"{self.spin_derate.value()}%")
        if self.spin_cover.value() != 100:
            _add_req("Min CFM Coverage", f"{self.spin_cover.value()}%")
        _add_req("Options Found", str(len(multi)))

        doc.add_paragraph()

        best = multi.iloc[0]
        doc.add_heading("Recommended Combination", level=1)
        rec = doc.add_paragraph()
        rec.add_run(f"{int(best['Units'])} × ").bold = True
        rec.add_run(f"{best['System Model']}").bold = True
        rec.add_run(f"   ({best['Series']}, {best['Category']})")

        detail = doc.add_paragraph(
            f"Total capacity: {best['Total Btu']:,.0f} Btu/hr   "
            f"(single unit {best['Unit Btu']:,.0f} Btu/hr, "
            f"excess {best['Excess %']:.1f}%)\n"
            f"Total airflow: {best['Total CFM']:,.0f} CFM   "
            f"(single unit {best['Unit CFM']:,.0f} CFM)"
        )
        for r in detail.runs:
            r.font.size = Pt(10)

        doc.add_paragraph()

        doc.add_heading("All Combinations", level=1)
        cols = ["#", "Units", "Category", "Series", "System Model",
                "Unit Btu", "Total Btu", "Excess %",
                "Unit CFM", "Total CFM", "EER", "Type"]
        tbl = doc.add_table(rows=1, cols=len(cols))
        tbl.style = "Light Grid Accent 1"
        hdr = tbl.rows[0].cells
        for i, name in enumerate(cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(9)

        for i, (_, row) in enumerate(multi.iterrows(), start=1):
            cells = tbl.add_row().cells
            eer = row.get('EER')
            cells[0].text = str(i)
            cells[1].text = str(int(row['Units']))
            cells[2].text = str(row.get('Category', ''))
            cells[3].text = str(row.get('Series', ''))
            cells[4].text = str(row.get('System Model', ''))
            cells[5].text = f"{row['Unit Btu']:,.0f}"
            cells[6].text = f"{row['Total Btu']:,.0f}"
            cells[7].text = f"{row['Excess %']:.1f}%"
            cells[8].text = f"{row['Unit CFM']:,.0f}"
            cells[9].text = f"{row['Total CFM']:,.0f}"
            cells[10].text = "—" if pd.isna(eer) else f"{eer:g}"
            cells[11].text = str(row.get('Type', ''))
            for c in cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(9)

        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            "Generated by Carrier AC Selector — HAP Report Analyzer"
        )
        run.italic = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

        self._roi_append_to_docx(doc)
        self._elec_append_to_docx(doc)

        doc.save(path)

    def clear_all(self):
        self.current_btu      = 0.0
        self.current_cfm      = 0.0
        self.current_sensible = 0.0
        self.current_results  = None
        self.source_basename  = ""
        self.current_system_name = ""
        self.picked_df_index  = None
        self.systems          = []
        self.batch_results    = None
        self.selected_units   = {}

        self.lbl_image.clear()
        self.lbl_image.setText("No image loaded")
        self.lbl_btu.setText("Cooling Load:  —")
        self.lbl_cfm.setText("Airflow:  —")
        for lbl in (self.lbl_btu, self.lbl_cfm):
            lbl.setStyleSheet("")
        self.input_btu.clear()
        self.input_cfm.clear()
        self.input_sensible.clear()
        self.lbl_ocr_status.setText("Waiting for OCR…")
        self.lbl_ocr_status.setStyleSheet("color: #9ca3af; font-size: 11px; padding: 2px 0;")
        self.txt_debug.clear()

        # filters back to defaults (values are already 0, so no re-search fires)
        self.combo_category.setCurrentIndex(0)
        self.combo_type.setCurrentIndex(0)
        self.combo_series.setCurrentIndex(0)
        self.combo_power.setCurrentIndex(0)
        self.spin_safety.setValue(0)
        self.spin_cover.setValue(100)
        self.spin_derate.setValue(0)

        self.table.setRowCount(0)
        self.table_multi.setRowCount(0)
        if hasattr(self, "table_batch"):
            self.table_batch.setRowCount(0)
            self.lbl_batch_hint.setText(
                "Upload a full HAP Systems Design Report — every air system "
                "is detected automatically and the best unit is picked for each.")
        self.lbl_multi_hint.setText(
            "When no single unit covers the required load, this tab suggests "
            "the minimum number of identical units needed.")
        self.lbl_count.setText("No results yet — upload a HAP report to begin.")
        self.lbl_count.setStyleSheet("color: #6b7280; font-size: 12px; padding: 2px 4px;")
        self.btn_export.setEnabled(False)
        self.btn_export_sel.setEnabled(False)
        self._render_selected_table()
        self._set_status("Cleared — ready for a new HAP report.")

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────
    def _update_extracted_labels(self):
        ok = "color: #34d399; font-weight: bold; font-size: 12px;"
        warn = "color: #f59e0b; font-weight: bold; font-size: 12px;"
        if self.current_btu > 0:
            tons = self.current_btu / core.BTU_PER_TON
            kw   = self.current_btu / core.BTU_PER_KW
            self.lbl_btu.setText(
                f"Cooling Load:  {self.current_btu:,.0f} Btu/hr\n"
                f"  ({tons:.2f} Tons  |  {kw:.2f} kW)")
            self.lbl_btu.setStyleSheet(ok)
        else:
            self.lbl_btu.setText("Cooling Load:  ⚠  Not set")
            self.lbl_btu.setStyleSheet(warn)
        if self.current_cfm > 0:
            self.lbl_cfm.setText(f"Airflow:  {self.current_cfm:,.0f} CFM")
            self.lbl_cfm.setStyleSheet(ok)
        else:
            self.lbl_cfm.setText("Airflow:  ⚠  Not set")
            self.lbl_cfm.setStyleSheet(warn)

    def _set_status(self, msg: str):
        self.status_bar.showMessage(msg)

    # ── Inverter ROI helpers (optional, safe) ──────────────────────
    def _roi_inputs_results(self):
        """Return (inputs, results) from the ROI tab or (None, None)."""
        calc = getattr(self, "roi_calculator", None)
        if not ROI_AVAILABLE or calc is None:
            return None, None
        try:
            inp = calc.get_inputs()
            res = roi_tab.compute_roi(
                **{k: v for k, v in inp.items() if k != "currency"})
            return inp, res
        except Exception:
            logger.exception("Failed to calculate ROI data for export")
            raise

    def _roi_append_to_excel(self, path: str):
        """Append the ROI sheet to `path` if the checkbox is ticked."""
        calc = getattr(self, "roi_calculator", None)
        if not ROI_AVAILABLE or calc is None:
            return
        if not path.lower().endswith(".xlsx"):
            return
        if not calc.is_excel_export_enabled():
            return
        inp, res = self._roi_inputs_results()
        if inp is None:
            return
        try:
            append_roi_sheet_to_excel(path, inp, res)
        except Exception:
            logger.exception("Failed to append ROI sheet during Excel export: %s", path)
            raise

    def _roi_append_to_docx(self, doc):
        """Append the ROI section to an in-memory docx Document."""
        calc = getattr(self, "roi_calculator", None)
        if not ROI_AVAILABLE or calc is None:
            return
        if not calc.is_word_export_enabled():
            return
        inp, res = self._roi_inputs_results()
        if inp is None:
            return
        try:
            append_roi_section_to_docx(doc, inp, res)
        except Exception:
            logger.exception("Failed to append ROI section during Word export")
            raise

    def _roi_update_capacity(self, btu: float, units: int = 1):
        """Push the freshly read / selected load into the ROI tab."""
        calc = getattr(self, "roi_calculator", None)
        if not ROI_AVAILABLE or calc is None:
            return
        try:
            if btu and btu > 0:
                calc.set_capacity_from_load(float(btu), units=int(units))
        except Exception:
            pass

    # ── Electrical Load helpers (optional, safe) ───────────────────
    def _elec_inputs_results(self):
        tab = getattr(self, "electrical_tab", None)
        if not ELEC_AVAILABLE or tab is None:
            return None, None
        try:
            return getattr(tab, "last_results", (None, None))
        except Exception:
            logger.exception("Failed to read electrical data for export")
            raise

    def _elec_append_to_excel(self, path: str):
        tab = getattr(self, "electrical_tab", None)
        if not ELEC_AVAILABLE or tab is None: return
        if not path.lower().endswith(".xlsx"): return
        if not tab.is_excel_export_enabled(): return
        units, summary = self._elec_inputs_results()
        if not units: return
        try:
            append_electrical_sheet_to_excel(path, units, summary)
        except Exception:
            logger.exception("Failed to append electrical sheet during Excel export: %s", path)
            raise

    def _elec_append_to_docx(self, doc):
        tab = getattr(self, "electrical_tab", None)
        if not ELEC_AVAILABLE or tab is None: return
        if not tab.is_word_export_enabled(): return
        units, summary = self._elec_inputs_results()
        if not units: return
        try:
            append_electrical_section_to_docx(doc, units, summary)
        except Exception:
            logger.exception("Failed to append electrical section during Word export")
            raise

    def _elec_sync_from_cart(self):
        tab = getattr(self, "electrical_tab", None)
        if not ELEC_AVAILABLE or tab is None: return
        try:
            tab.sync_units(
                self.selected_units,
                fallback_btu=self.current_btu,
                fallback_ps="220V / 1Ph",
            )
        except Exception:
            pass

    # ──────────────────────────────────────────────────────────────
    # Shutdown / drag-and-drop
    # ──────────────────────────────────────────────────────────────
    _DROP_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
                  ".pdf", ".rtf")

    def closeEvent(self, event):
        """Stop the OCR thread cleanly so Qt doesn't abort on shutdown."""
        w = getattr(self, "ocr_worker", None)
        if w is not None and w.isRunning():
            for sig in (w.done, w.error, w.progress):
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            if not w.wait(30000):      # give Tesseract time to finish
                w.terminate()
                w.wait()
        event.accept()

    def dragEnterEvent(self, event):
        if not self.btn_upload.isEnabled():
            return
        md = event.mimeData()
        if not md.hasUrls():
            return
        for url in md.urls():
            if url.toLocalFile().lower().endswith(self._DROP_EXTS):
                event.acceptProposedAction()
                return

    def dropEvent(self, event):
        if not self.btn_upload.isEnabled():
            return
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(self._DROP_EXTS):
                self._load_report(p)
                return

    def _on_table_context_menu(self, pos):
        """Right-click menu on the results table."""
        item = self.table.itemAt(pos)
        if item is None:
            return
        row = item.row()

        menu = QMenu(self)
        act_model  = menu.addAction("📋  Copy System Model")
        act_row    = menu.addAction("📄  Copy Row (TSV)")
        menu.addSeparator()
        act_cart   = menu.addAction("🛒  Add to Selection")
        menu.addSeparator()
        act_export = menu.addAction("💾  Export Selected…")
        act_export.setEnabled(self.btn_export_sel.isEnabled())

        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        if chosen is act_cart:
            self._add_to_selection()
            return
        elif chosen is act_model:
            mi = self.table.item(row, 2)          # column 2 = System Model
            if mi:
                QApplication.clipboard().setText(mi.text())
                self._set_status(f"Copied: {mi.text()}")
        elif chosen is act_row:
            cells = []
            for col in range(self.table.columnCount()):
                it = self.table.item(row, col)
                cells.append(it.text() if it else "")
            QApplication.clipboard().setText("	".join(cells))
            self._set_status(f"Copied row {row + 1} (TSV).")
        elif chosen is act_export:
            self.table.clearSelection()
            for col in range(self.table.columnCount()):
                it = self.table.item(row, col)
                if it:
                    it.setSelected(True)
            self.export_selected()

    def _copy_model_on_dblclick(self, item: QTableWidgetItem):
        """Double-clicking any cell copies that row's System Model column."""
        model_item = self.table.item(item.row(), 2)   # column 2 = System Model
        if model_item:
            QApplication.clipboard().setText(model_item.text())
            self._set_status(f"Copied to clipboard: {model_item.text()}")


    # ══════════════════════════════════════════════════════════════
    #  [NEW / OPTIONAL]  Selection Cart
    #  لا يؤثر على Export Picked / Export All / Export Selected
    # ══════════════════════════════════════════════════════════════
    def _add_to_selection(self):
        """Add the picked (or table-selected) row to the selection cart."""
        df = self.current_results
        if df is None or df.empty:
            # If multi-unit options exist, offer to jump to that tab
            if (hasattr(self, "table_multi")
                    and self.table_multi.rowCount() > 0):
                reply = QMessageBox.question(
                    self, "No Single Unit",
                    "مفيش وحدة منفردة تغطى الحمل، لكن فيه خيارات Multi-Unit.\n"
                    "تفتح تاب Multi-Unit وتضيف من هناك؟",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    self.tabs.setCurrentWidget(self.tab_multi)
                return
            QMessageBox.information(self, "No Results",
                "اعمل بحث الأول (Pick Best أو اختار صف).")
            return

        idx = None
        sel_rows = {it.row() for it in self.table.selectedItems()}
        if sel_rows:
            item0 = self.table.item(min(sel_rows), 0)
            if item0 is not None:
                idx = item0.data(Qt.UserRole)
        if idx is None:
            idx = self.picked_df_index
        if idx is None or idx not in df.index:
            QMessageBox.information(self, "Nothing Picked",
                "اضغط “Pick Best” أو اختار صف، بعدين “Add to Selection”.")
            return

        row_data = df.loc[idx]
        sys_name = (self.current_system_name
                    or self.source_basename
                    or f"System {len(self.selected_units) + 1}")

        existed = sys_name in self.selected_units
        self.selected_units[sys_name] = {
            "units": 1,
            "row": row_data,
            "load_btu": self.current_btu,
            "load_cfm": self.current_cfm,
            "load_sensible": self.current_sensible,
        }
        self._render_selected_table()

        verb = "تحديث" if existed else "إضافة"
        self._set_status(
            f"🛒  {verb}: {row_data['System Model']} لـ {sys_name}  —  "
            f"{len(self.selected_units)} system(s) في السلة.")

    def _add_multi_to_selection(self):
        """Add the selected (or best) multi-unit option to the cart."""
        table = self.table_multi
        if table.rowCount() == 0:
            QMessageBox.information(self, "No Multi-Unit Options",
                "مفيش خيارات multi-unit متاحة — عدّل الفلاتر وجرّب تانى.")
            return

        row = table.currentRow()
        if row < 0:
            row = 0  # default to the best option (first row, already sorted)

        model_item = table.item(row, 3)   # col 3 = System Model
        units_item = table.item(row, 0)   # col 0 = # Units
        if model_item is None or units_item is None:
            QMessageBox.warning(self, "Could Not Read Row",
                "الصف المحدد غير قابل للقراءة. جرّب تحدد صف تانى.")
            return
        try:
            model_name = model_item.text()
            n_units = int(float(units_item.text()))
        except (ValueError, AttributeError):
            QMessageBox.warning(self, "Bad Row Data",
                "البيانات في الصف المحدد مش قابلة للقراءة.")
            return

        ac_row = self.df[self.df["System Model"] == model_name]
        if ac_row.empty:
            QMessageBox.warning(self, "Unit Not Found",
                f"مش لاقي الموديل {model_name} في الداتابيز.")
            return
        row_data = ac_row.iloc[0]

        sys_name = (self.current_system_name
                    or self.source_basename
                    or f"System {len(self.selected_units) + 1}")

        existed = sys_name in self.selected_units
        self.selected_units[sys_name] = {
            "units": int(n_units),
            "row": row_data,
            "load_btu": self.current_btu,
            "load_cfm": self.current_cfm,
            "load_sensible": self.current_sensible,
        }
        self._render_selected_table()

        verb = "تحديث" if existed else "إضافة"
        self._set_status(
            f"🛒  {verb}: {n_units}x {model_name} لـ {sys_name}  —  "
            f"{len(self.selected_units)} system(s) في السلة.")
        self.tabs.setCurrentWidget(self.tab_selected)

    def _render_selected_table(self):
        if not hasattr(self, "table_selected"):
            return
        cols = ['System', 'Load (Btu/hr)', 'Load (Tons)', 'Load (CFM)',
                'Sensible (Btu/hr)',
                'Units', 'Category', 'Series', 'System Model',
                'Indoor Unit', 'Outdoor Unit',
                'Unit Btu', 'Unit CFM', 'Total Btu', 'Total CFM',
                'Excess %', 'EER', 'Type', 'Power Supply']
        self.table_selected.setSortingEnabled(False)
        self.table_selected.setColumnCount(len(cols))
        self.table_selected.setHorizontalHeaderLabels(cols)
        self.table_selected.setRowCount(len(self.selected_units))

        for r_i, (sys_name, entry) in enumerate(self.selected_units.items()):
            n_units   = entry["units"]
            r         = entry["row"]
            load_btu  = float(entry.get("load_btu", 0) or 0)
            load_cfm  = float(entry.get("load_cfm", 0) or 0)
            load_sens = float(entry.get("load_sensible", 0) or 0)
            unit_btu  = float(r['Cooling Capacity (Btu/hr)'])
            unit_cfm  = float(r['High CFM (numeric)'])
            total_btu = unit_btu * n_units
            total_cfm = unit_cfm * n_units
            excess = ((total_btu - load_btu) / load_btu * 100.0
                      if load_btu > 0 else float("nan"))
            eer = r['EER (Btu/Wh)']
            cells = [
                (sys_name, None),
                (f"{load_btu:,.0f}" if load_btu > 0 else "—",
                 load_btu if load_btu > 0 else None),
                (f"{load_btu/core.BTU_PER_TON:.2f}" if load_btu > 0 else "—",
                 load_btu if load_btu > 0 else None),
                (f"{load_cfm:,.0f}" if load_cfm > 0 else "—",
                 load_cfm if load_cfm > 0 else None),
                (f"{load_sens:,.0f}" if load_sens > 0 else "—",
                 load_sens if load_sens > 0 else None),
                (f"{n_units}x", float(n_units)),
                (self._txt(r['Category']), None),
                (self._txt(r['Series']), None),
                (self._txt(r['System Model']), None),
                (self._txt(r['Indoor Unit Model']), None),
                (self._txt(r['Outdoor Unit Model']), None),
                (f"{unit_btu:,.0f}", unit_btu),
                (f"{unit_cfm:,.0f}", unit_cfm),
                (f"{total_btu:,.0f}", total_btu),
                (f"{total_cfm:,.0f}", total_cfm),
                ("—" if pd.isna(excess) else f"{excess:.1f} %",
                 None if pd.isna(excess) else excess),
                ("" if pd.isna(eer) else f"{eer:g}",
                 None if pd.isna(eer) else float(eer)),
                (self._txt(r['Type']), None),
                (self._txt(r['Power Supply']), None),
            ]
            for c_i, (txt, val) in enumerate(cells):
                self.table_selected.setItem(r_i, c_i, self._num_item(txt, val))
        self.table_selected.setSortingEnabled(True)

        n = len(self.selected_units)
        if hasattr(self, "tab_selected"):
            idx = self.tabs.indexOf(self.tab_selected)
            if idx >= 0:
                self.tabs.setTabText(idx, f"🛒  Selected ({n})")
        if n:
            self.lbl_selected_hint.setText(
                f"{n} unit(s) في السلة — اضغط “Export All Selected” "
                f"لحفظهم كلهم في ملف واحد.")
        else:
            self.lbl_selected_hint.setText(
                "اختيارى:  اختار وحدة واضغط “Add to Selection”.")
        self.btn_export_cart.setEnabled(n > 0)
        self.btn_remove_cart.setEnabled(n > 0)
        self.btn_clear_cart.setEnabled(n > 0)
        self._elec_sync_from_cart()

    def _remove_from_cart(self):
        rows = {it.row() for it in self.table_selected.selectedItems()}
        if not rows:
            return
        names = []
        for row in rows:
            it = self.table_selected.item(row, 0)
            if it:
                names.append(it.text())
        for name in names:
            self.selected_units.pop(name, None)
        self._render_selected_table()
        self._set_status(f"حذف {len(names)} system(s) من السلة.")

    def _clear_cart(self):
        if not self.selected_units:
            return
        reply = QMessageBox.question(self, "Clear Selection",
            f"حذف {len(self.selected_units)} system(s) من السلة؟",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.selected_units.clear()
        self._render_selected_table()
        self._set_status("اتفضت السلة.")

    def _prepare_cart_export_df(self):
        rows = []
        for sys_name, entry in self.selected_units.items():
            r         = entry["row"]
            n_units   = entry["units"]
            load_btu  = float(entry.get("load_btu", 0) or 0)
            load_cfm  = float(entry.get("load_cfm", 0) or 0)
            load_sens = float(entry.get("load_sensible", 0) or 0)
            unit_btu  = float(r['Cooling Capacity (Btu/hr)'])
            unit_cfm  = float(r['High CFM (numeric)'])
            total_btu = unit_btu * n_units
            total_cfm = unit_cfm * n_units
            excess = ((total_btu - load_btu) / load_btu * 100.0
                      if load_btu > 0 else float("nan"))
            cfm_cover = (total_cfm / load_cfm * 100.0
                         if load_cfm > 0 else float("nan"))

            d = r.to_dict()
            d["System"]                  = sys_name
            d["Load (Btu/hr)"]           = load_btu
            d["Load (Tons)"]             = load_btu / core.BTU_PER_TON
            d["Load (CFM)"]              = load_cfm
            d["Sensible (Btu/hr)"]       = load_sens
            d["Units"]                   = n_units
            d["Unit Capacity (Btu/hr)"]  = unit_btu
            d["Unit CFM"]                = unit_cfm
            d["Total Capacity (Btu/hr)"] = total_btu
            d["Total CFM"]               = total_cfm
            d["Excess %"]                = excess
            d["CFM Cover %"]             = cfm_cover
            rows.append(d)

        out = pd.DataFrame(rows)
        head = ["System",
                "Load (Btu/hr)", "Load (Tons)", "Load (CFM)",
                "Sensible (Btu/hr)",
                "Units",
                "Unit Capacity (Btu/hr)", "Unit CFM",
                "Total Capacity (Btu/hr)", "Total CFM",
                "Excess %", "CFM Cover %"]

        # ترتيب منظم بدون تكرار واستبعاد الأعمدة التقنية مثل CFM Approx
        clean_specs = [
            "Category", "Series", "System Model", "Indoor Unit Model", "Outdoor Unit Model",
            "Type", "Power Supply", "EER (Btu/Wh)",
            "Heating Capacity (Btu/hr)", "Heating Capacity (kW)", "Airflow (m3/h)",
            "Indoor Unit Dimensions (mm)", "Indoor Unit Weight (kg)",
            "Outdoor Unit Dimensions (mm)", "Outdoor Unit Weight (kg)", "Outdoor Sound dB(A)",
            "Approx. Area (m2)*", "Notes"
        ]
        cols = head + [c for c in clean_specs if c in out.columns]
        out = out[cols]

        for col, nd in (("Excess %", 1), ("CFM Cover %", 0),
                        ("Load (Btu/hr)", 0), ("Load (Tons)", 2),
                        ("Load (CFM)", 0), ("Sensible (Btu/hr)", 0),
                        ("Unit Capacity (Btu/hr)", 0), ("Unit CFM", 0),
                        ("Total Capacity (Btu/hr)", 0), ("Total CFM", 0)):
            if col in out.columns:
                out[col] = out[col].round(nd)

        out = out.fillna("")
        # تنظيف كل النصوص من أي إيموجي أو رموز تماماً
        for col in out.columns:
            out[col] = out[col].apply(
                lambda v: re.sub(r'[\U00010000-\U0010ffff]|[\u2600-\u27bf]', '',
                                 str(v).replace('✔', 'OK').replace('✘', 'Low')).strip()
                if isinstance(v, str) else v
            )
        return out

    def _export_cart(self):
        if not self.selected_units:
            QMessageBox.warning(self, "Empty Selection",
                                "السلة فاضية — ضيف وحدات الأول.")
            return
        default_name = (f"{self.source_basename or 'Carrier'}"
                        f"_Selection_All_{len(self.selected_units)}"
                        f"_systems.xlsx")
        path, selected_filter = self._save_file_dialog(
            "Save All Selected Units", default_name,
            "Excel Workbook (*.xlsx);;Word Report (*.docx);;CSV File (*.csv)")
        if not path:
            return
        path = self._normalize_save_path(path, selected_filter)
        ext = os.path.splitext(path)[1].lower()
        try:
            with _atomic_export(path) as temporary_path:
                if ext == ".docx":
                    self._build_cart_word_report(temporary_path)
                elif ext == ".csv":
                    self._prepare_cart_export_df().to_csv(
                        temporary_path, index=False, encoding="utf-8-sig")
                else:
                    self._prepare_cart_export_df().to_excel(temporary_path, index=False)
                    _style_excel_file(temporary_path)
                    self._roi_append_to_excel(temporary_path)
                    self._elec_append_to_excel(temporary_path)
            self._set_status(f"✅  {len(self.selected_units)} system(s) → {path}")
            QMessageBox.information(self, "Export Successful",
                f"{len(self.selected_units)} system(s) saved to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", _export_error_message(exc))

    def _build_cart_word_report(self, path):
        if not DOCX_AVAILABLE:
            raise RuntimeError(
                "python-docx is not installed.\n"
                "  python -m pip install python-docx")
        from datetime import datetime
        from docx.enum.section import WD_ORIENT, WD_SECTION
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        doc = Document()
        # First section: LANDSCAPE so the summary table fits on one page
        s0 = doc.sections[0]
        s0.orientation = WD_ORIENT.LANDSCAPE
        pw, ph = s0.page_width, s0.page_height
        if pw < ph:
            s0.page_width, s0.page_height = ph, pw
        s0.left_margin   = Inches(0.4)
        s0.right_margin  = Inches(0.4)
        s0.top_margin    = Inches(0.5)
        s0.bottom_margin = Inches(0.5)
        h = doc.add_heading("Carrier AC — Selection Report (All Systems)", level=0)

        h.paragraph_format.keep_with_next = True
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub = doc.add_paragraph(
            f"{len(self.selected_units)} system(s)  •  source: "
            f"{self.source_basename or 'N/A'}")
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        sub.runs[0].italic = True
        date_p = doc.add_paragraph(
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        date_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for r in date_p.runs:
            r.font.size = Pt(9)
            r.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        _sh = doc.add_heading("Summary", level=1)
        _sh.paragraph_format.space_before = Pt(4)
        _sh.paragraph_format.space_after  = Pt(4)
        _sh.paragraph_format.keep_with_next = True
        cols = ["System", "Load (Btu/hr)", "Load (CFM)", "Units",
                "Type", "Series",
                "System Model", "Indoor Unit", "Outdoor Unit",
                "Total Btu/hr", "Total CFM", "Excess %", "EER"]
        col_rel = [0.55, 0.72, 0.62, 0.42,
                   0.85, 1.15,
                   1.05, 1.00, 1.00,
                   0.78, 0.72, 0.60, 0.42]

        tbl = doc.add_table(rows=1, cols=len(cols))
        tbl.style = "Light Grid Accent 1"
        tbl.autofit = False
        tblPr = tbl._tbl.tblPr
        layout = OxmlElement('w:tblLayout')
        layout.set(qn('w:type'), 'fixed')
        tblPr.append(layout)

        hdr = tbl.rows[0].cells
        for i, name in enumerate(cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(7.5)
        for sys_name, entry in self.selected_units.items():
            n_units   = entry["units"]
            r         = entry["row"]
            load_btu  = float(entry.get("load_btu", 0) or 0)
            load_cfm  = float(entry.get("load_cfm", 0) or 0)
            btu = float(r['Cooling Capacity (Btu/hr)']) * n_units
            cfm = float(r['High CFM (numeric)']) * n_units
            excess = ((btu - load_btu) / load_btu * 100.0
                      if load_btu > 0 else float("nan"))
            eer = r['EER (Btu/Wh)']
            cells = tbl.add_row().cells
            cells[0].text = sys_name
            cells[1].text = f"{load_btu:,.0f}" if load_btu > 0 else "\u2014"
            cells[2].text = f"{load_cfm:,.0f}" if load_cfm > 0 else "\u2014"
            cells[3].text = f"{n_units}x"
            cells[4].text = self._txt(r['Type'])
            cells[5].text = self._txt(r['Series'])
            cells[6].text = self._txt(r['System Model'])
            cells[7].text = self._txt(r['Indoor Unit Model'])
            cells[8].text = self._txt(r['Outdoor Unit Model'])
            cells[9].text  = f"{btu:,.0f}"
            cells[10].text = f"{cfm:,.0f}"
            cells[11].text = "\u2014" if pd.isna(excess) else f"{excess:.1f}%"
            cells[12].text = "" if pd.isna(eer) else f"{eer:g}"
            for c in cells:
                for p in c.paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(7.5)

        usable_emu = s0.page_width - s0.left_margin - s0.right_margin
        rel_total  = sum(col_rel)
        for row in tbl.rows:
            for i, c in enumerate(row.cells):
                c.width = int(usable_emu * col_rel[i] / rel_total)
        doc.add_paragraph()
        # ── Section break: back to PORTRAIT for spec sheets ──
        sec2 = doc.add_section(WD_SECTION.NEW_PAGE)
        sec2.orientation = WD_ORIENT.PORTRAIT
        pw2, ph2 = sec2.page_width, sec2.page_height
        if pw2 > ph2:
            sec2.page_width, sec2.page_height = ph2, pw2
        sec2.left_margin   = Inches(0.8)
        sec2.right_margin  = Inches(0.8)
        sec2.top_margin    = Inches(0.8)
        sec2.bottom_margin = Inches(0.8)

        doc.add_heading("Unit Specifications per System", level=1)
        _first_spec = True  # PATCH5_MARKER
        for sys_name, entry in self.selected_units.items():
            n_units = entry["units"]
            r = entry["row"]
            title_units = f"  ({n_units}x)" if n_units > 1 else ""
            if not _first_spec:
                doc.add_page_break()
            _first_spec = False
            doc.add_heading(
                f"{sys_name} — {r['System Model']}{title_units}", level=2)
            spec_tbl = doc.add_table(rows=0, cols=2)
            spec_tbl.style = "Light List Accent 1"
            def add_spec(label, value):
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    return
                s = str(value).strip()
                if s == "" or s.lower() == "nan":
                    return
                c = spec_tbl.add_row().cells
                c[0].text = label
                c[1].text = s
                for p in c[0].paragraphs:
                    for run in p.runs:
                        run.bold = True
                        run.font.size = Pt(10)
                for p in c[1].paragraphs:
                    for run in p.runs:
                        run.font.size = Pt(10)
            add_spec("System",        sys_name)
            load_btu  = entry.get("load_btu", 0) or 0
            load_cfm  = entry.get("load_cfm", 0) or 0
            load_sens = entry.get("load_sensible", 0) or 0
            if load_btu > 0:
                add_spec("Required Cooling Load",
                         f"{load_btu:,.0f} Btu/hr  "
                         f"({load_btu/core.BTU_PER_TON:.2f} Tons | "
                         f"{load_btu/core.BTU_PER_KW:.2f} kW)")
            if load_cfm > 0:
                add_spec("Required Airflow (CFM)", f"{load_cfm:,.0f}")
            if load_sens > 0:
                add_spec("Required Sensible Load",
                         f"{load_sens:,.0f} Btu/hr")
            if n_units > 1:
                add_spec("Number of Identical Units", f"{n_units}")
            add_spec("Category",      r.get('Category', ''))
            add_spec("Series",        r.get('Series', ''))
            add_spec("System Model",  r.get('System Model', ''))
            add_spec("Indoor Unit",   r.get('Indoor Unit Model', ''))
            add_spec("Outdoor Unit",  r.get('Outdoor Unit Model', ''))
            add_spec("Type",          r.get('Type', ''))
            btu_v = self._to_float(r.get('Cooling Capacity (Btu/hr)', 0)) or 0.0
            if btu_v > 0:
                kw_v = btu_v / core.BTU_PER_KW
                if n_units > 1:
                    add_spec("Unit Cooling Capacity (each)",
                             f"{btu_v:,.0f} Btu/hr  "
                             f"({btu_v/core.BTU_PER_TON:.2f} Tons | {kw_v:.2f} kW)")
                    tot_btu = btu_v * n_units
                    tot_kw  = tot_btu / core.BTU_PER_KW
                    add_spec(f"Total Cooling Capacity ({n_units}x)",
                             f"{tot_btu:,.0f} Btu/hr  "
                             f"({tot_btu/core.BTU_PER_TON:.2f} Tons | {tot_kw:.2f} kW)")
                else:
                    add_spec("Cooling Capacity",
                             f"{btu_v:,.0f} Btu/hr  "
                             f"({btu_v/core.BTU_PER_TON:.2f} Tons | {kw_v:.2f} kW)")
            h_btu = self._to_float(r.get('Heating Capacity (Btu/hr)', None))
            if h_btu is not None and h_btu > 0:
                add_spec("Heating Capacity", f"{h_btu:,.0f} Btu/hr")
            add_spec("Airflow (CFM) L/M/H", r.get('Airflow (CFM) L/M/H', ''))
            add_spec("Airflow (m3/h)",      r.get('Airflow (m3/h)', ''))
            add_spec("EER (Btu/Wh)",        r.get('EER (Btu/Wh)', ''))
            add_spec("Power Supply",        r.get('Power Supply', ''))
            add_spec("Indoor Unit Dimensions",
                     r.get('Indoor Unit Dimensions (mm)', ''))
            add_spec("Indoor Unit Weight",  r.get('Indoor Unit Weight (kg)', ''))
            add_spec("Outdoor Unit Dimensions",
                     r.get('Outdoor Unit Dimensions (mm)', ''))
            add_spec("Outdoor Unit Weight",  r.get('Outdoor Unit Weight (kg)', ''))
            add_spec("Outdoor Sound Level",  r.get('Outdoor Sound dB(A)', ''))
            add_spec("Approx. Area (m2)",   self._fmt_num(r.get('Approx. Area (m2)*')))
            notes = r.get('Notes', '')
            if notes and not (isinstance(notes, float) and pd.isna(notes)):
                add_spec("Notes", str(notes))
            # Prevent rows from splitting across pages
            for _row in spec_tbl.rows:
                _trPr = _row._tr.get_or_add_trPr()
                _trPr.append(OxmlElement("w:cantSplit"))
            doc.add_paragraph()
        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            "Generated by Carrier AC Selector — HAP Report Analyzer")
        run.italic = True
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

        self._roi_append_to_docx(doc)
        self._elec_append_to_docx(doc)

        doc.save(path)


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 12))
    window = HAPToCarrierApp()
    window.show()

    sys.exit(app.exec())
