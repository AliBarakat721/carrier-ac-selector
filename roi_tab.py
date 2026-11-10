"""
Inverter ROI & Energy Saving Calculator
=======================================
Pure calculation logic, Excel/Word builders, and PySide6 interactive widget.
"""
from __future__ import annotations

import re
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Optional
import pandas as pd

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QSlider,
    QPushButton, QCheckBox, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QApplication, QFileDialog
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QBrush

logger = logging.getLogger(__name__)


@contextmanager
def _atomic_export(path: str):
    """Replace the final export only after every export step succeeds."""
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


# ── Pure Calculation Logic ─────────────────────────────────────────────────────

def compute_roi(
    btu: float,
    units: int = 1,
    hours_per_day: float = 8.0,
    days_per_year: int = 180,
    tariff: float = 1.50,
    fixed_unit_price: float = 18000.0,
    inv_unit_price: float = 26500.0,
    install_delta: float = 0.0,
    saving_pct: float = 30.0,
    maintenance_saving_pct: float = 0.0,
    lifetime_years: int = 12,
    co2_factor: float = 0.30,
    kw_per_ton: float = 1.20,
) -> dict:
    """Calculate energy consumption, costs, payback period and lifetime savings."""
    units = max(1, int(units))
    annual_hours = hours_per_day * days_per_year
    tons = (btu * units) / 12000.0 if btu > 0 else 0.0

    # Power Input (kW)
    fixed_kw = tons * kw_per_ton
    inverter_kw = fixed_kw * (1.0 - (saving_pct / 100.0))

    # Annual Consumption (kWh)
    fixed_annual_kwh = fixed_kw * annual_hours
    inverter_annual_kwh = inverter_kw * annual_hours
    annual_kwh_saving = max(0.0, fixed_annual_kwh - inverter_annual_kwh)

    # Annual Energy Bills
    fixed_energy_bill = fixed_annual_kwh * tariff
    inverter_energy_bill = inverter_annual_kwh * tariff
    annual_energy_saving = max(0.0, fixed_energy_bill - inverter_energy_bill)

    # Maintenance assumptions (optional)
    maint_saving = 0.0
    if maintenance_saving_pct > 0:
        base_maint = (fixed_unit_price * units) * 0.03
        maint_saving = base_maint * (maintenance_saving_pct / 100.0)

    total_annual_saving = annual_energy_saving + maint_saving

    # Upfront Costs & Price Premium
    fixed_upfront = fixed_unit_price * units
    inverter_upfront = (inv_unit_price * units) + install_delta
    price_premium = inverter_upfront - fixed_upfront

    # Payback Period
    if total_annual_saving > 0 and price_premium > 0:
        payback_years = price_premium / total_annual_saving
    elif price_premium <= 0 and total_annual_saving > 0:
        payback_years = 0.0
    else:
        payback_years = None

    # Lifetime Financials
    fixed_lifetime_cost = fixed_upfront + (fixed_energy_bill * lifetime_years)
    inverter_lifetime_cost = (
        inverter_upfront + ((inverter_energy_bill - maint_saving) * lifetime_years)
    )
    lifetime_saving = fixed_lifetime_cost - inverter_lifetime_cost
    annual_co2_kg = annual_kwh_saving * co2_factor

    # Recommendation assessment
    if payback_years is None or total_annual_saving <= 0:
        rec_status = "none"
        rec_text = "⚠️ No financial savings detected with current inputs."
        rec_color = "#9ca3af"
    elif payback_years < 2.0:
        rec_status = "highly_recommended"
        rec_text = f"✅ Inverter is highly recommended (Fast payback: {payback_years:.1f} years)."
        rec_color = "#34d399"
    elif payback_years <= 4.0:
        rec_status = "recommended"
        rec_text = f"✅ Inverter is recommended (Payback within warranty: {payback_years:.1f} years)."
        rec_color = "#60a5fa"
    elif payback_years <= 6.0:
        rec_status = "marginal"
        rec_text = f"⚖️ Marginal — viable if daily operating hours increase (Payback: {payback_years:.1f} years)."
        rec_color = "#fbbf24"
    else:
        rec_status = "not_viable"
        rec_text = f"❌ Fixed Speed is more economical for this light usage profile (Payback: {payback_years:.1f} years)."
        rec_color = "#f87171"

    return {
        "tons": tons,
        "annual_hours": annual_hours,
        "fixed_kw": fixed_kw,
        "inverter_kw": inverter_kw,
        "fixed_annual_kwh": fixed_annual_kwh,
        "inverter_annual_kwh": inverter_annual_kwh,
        "annual_kwh_saving": annual_kwh_saving,
        "fixed_annual_cost": fixed_energy_bill,
        "inverter_annual_cost": inverter_energy_bill,
        "annual_saving": total_annual_saving,
        "fixed_upfront": fixed_upfront,
        "inverter_upfront": inverter_upfront,
        "price_premium": price_premium,
        "payback_years": payback_years,
        "fixed_lifetime_cost": fixed_lifetime_cost,
        "inverter_lifetime_cost": inverter_lifetime_cost,
        "lifetime_saving": lifetime_saving,
        "annual_co2_kg": annual_co2_kg,
        "rec_status": rec_status,
        "rec_text": rec_text,
        "rec_color": rec_color,
    }


def format_roi_markdown(inp: dict, res: dict) -> str:
    cur = inp.get("currency", "EGP")
    pb_str = f"{res['payback_years']:.1f} years" if res['payback_years'] is not None else "N/A"
    return f"""### Inverter vs Fixed Speed ROI Analysis
- **Cooling Capacity**: {inp['btu']:,.0f} BTU/hr ({res['tons']:.2f} Tons) × {inp['units']} Unit(s)
- **Operating Profile**: {inp['hours_per_day']} hrs/day × {inp['days_per_year']} days/yr ({res['annual_hours']:,.0f} hrs/year)
- **Electricity Tariff**: {inp['tariff']:.2f} {cur}/kWh | Efficiency Delta: {inp['saving_pct']}%

| Metric | Fixed Speed | Inverter | Difference / Savings |
| :--- | :---: | :---: | :---: |
| **Power Input (kW)** | {res['fixed_kw']:.2f} kW | {res['inverter_kw']:.2f} kW | -{res['fixed_kw'] - res['inverter_kw']:.2f} kW |
| **Annual Energy (kWh)** | {res['fixed_annual_kwh']:,.0f} kWh | {res['inverter_annual_kwh']:,.0f} kWh | {res['annual_kwh_saving']:,.0f} kWh/yr |
| **Annual Electricity Bill** | {res['fixed_annual_cost']:,.0f} {cur} | {res['inverter_annual_cost']:,.0f} {cur} | **{res['annual_saving']:,.0f} {cur}/yr** |
| **Upfront Equipment Cost** | {res['fixed_upfront']:,.0f} {cur} | {res['inverter_upfront']:,.0f} {cur} | +{res['price_premium']:,.0f} {cur} |
| **{inp['lifetime_years']}-Year Lifetime Cost** | {res['fixed_lifetime_cost']:,.0f} {cur} | {res['inverter_lifetime_cost']:,.0f} {cur} | **{res['lifetime_saving']:,.0f} {cur}** |

- **Payback Period**: **{pb_str}**
- **Annual CO₂ Avoided**: {res['annual_co2_kg']:,.0f} kg CO₂/year
- **Recommendation**: {res['rec_text']}
"""


# ── Excel / Word Integration Helpers ───────────────────────────────────────────

def append_roi_sheet_to_excel(excel_path: str, inp: dict, res: dict):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.load_workbook(excel_path)
        sheet_name = "Inverter_ROI"
        if sheet_name in wb.sheetnames:
            del wb[sheet_name]
        ws = wb.create_sheet(title=sheet_name)
        ws.views.sheetView[0].showGridLines = True

        cur = inp.get("currency", "EGP")
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        card_fill = PatternFill(start_color="F2F5F9", end_color="F2F5F9", fill_type="solid")
        highlight_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

        font_title = Font(name="Segoe UI", size=14, bold=True, color="1F4E79")
        font_hdr = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
        font_bold = Font(name="Segoe UI", size=10, bold=True)
        font_regular = Font(name="Segoe UI", size=9.5)

        thin = Side(style='thin', color='D9D9D9')
        border_all = Border(left=thin, right=thin, top=thin, bottom=thin)

        ws["A1"] = "Inverter vs Fixed Speed — Energy & Financial ROI Analysis"
        ws["A1"].font = font_title
        ws.row_dimensions[1].height = 26

        ws["A3"] = "Operating Conditions & Cost Assumptions"
        ws["A3"].font = font_bold
        inputs_data = [
            ("Total Cooling Capacity", f"{inp['btu']:,.0f} BTU/hr ({res['tons']:.2f} Tons)"),
            ("Units Count", f"{inp['units']} unit(s)"),
            ("Operating Hours / Day", f"{inp['hours_per_day']} hrs/day"),
            ("Operating Days / Year", f"{inp['days_per_year']} days/year ({res['annual_hours']:,.0f} hrs/yr)"),
            ("Electricity Tariff", f"{inp['tariff']:.2f} {cur}/kWh"),
            ("Inverter Energy Saving Assumption", f"{inp['saving_pct']:.0f} %"),
            ("System Lifetime", f"{inp['lifetime_years']} years"),
            ("Fixed Speed Unit Price", f"{inp['fixed_unit_price']:,.0f} {cur}"),
            ("Inverter Unit Price", f"{inp['inv_unit_price']:,.0f} {cur}"),
            ("Inverter Price Premium (Total)", f"{res['price_premium']:,.0f} {cur}"),
        ]
        for i, (k, v) in enumerate(inputs_data, start=4):
            ws.cell(row=i, column=1, value=k).font = font_regular
            c2 = ws.cell(row=i, column=2, value=v)
            c2.font = font_bold
            c2.alignment = Alignment(horizontal="right")
            ws.row_dimensions[i].height = 18

        table_start = len(inputs_data) + 6
        ws.cell(row=table_start - 1, column=1, value="Comparative Economic Results").font = font_bold

        headers = ["Metric", "Fixed Speed", "Inverter", f"Savings / Delta ({cur})"]
        ws.row_dimensions[table_start].height = 24
        for col_idx, h in enumerate(headers, start=1):
            c = ws.cell(row=table_start, column=col_idx, value=h)
            c.fill = header_fill
            c.font = font_hdr
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border = border_all

        table_rows = [
            ("Electrical Input Power (kW)", f"{res['fixed_kw']:.2f} kW", f"{res['inverter_kw']:.2f} kW", f"-{res['fixed_kw'] - res['inverter_kw']:.2f} kW"),
            ("Annual Energy Consumption (kWh)", f"{res['fixed_annual_kwh']:,.0f} kWh", f"{res['inverter_annual_kwh']:,.0f} kWh", f"{res['annual_kwh_saving']:,.0f} kWh"),
            ("Annual Electricity Bill", f"{res['fixed_annual_cost']:,.0f}", f"{res['inverter_annual_cost']:,.0f}", f"{res['annual_saving']:,.0f}"),
            ("Initial Upfront Equipment Cost", f"{res['fixed_upfront']:,.0f}", f"{res['inverter_upfront']:,.0f}", f"+{res['price_premium']:,.0f}"),
            (f"{inp['lifetime_years']}-Year Total Lifecycle Cost", f"{res['fixed_lifetime_cost']:,.0f}", f"{res['inverter_lifetime_cost']:,.0f}", f"{res['lifetime_saving']:,.0f}"),
        ]

        pb_str = f"{res['payback_years']:.1f} Years" if res['payback_years'] is not None else "N/A"
        summary_rows = [
            ("Payback Period", pb_str),
            ("Lifetime Financial Saving", f"{res['lifetime_saving']:,.0f} {cur}"),
            ("Annual CO₂ Emission Reduction", f"{res['annual_co2_kg']:,.0f} kg CO₂ / year"),
            ("Recommendation", res['rec_text']),
        ]

        for r_offset, (m, f_val, inv_val, d_val) in enumerate(table_rows, start=1):
            curr_row = table_start + r_offset
            ws.row_dimensions[curr_row].height = 20
            row_fill = card_fill if r_offset % 2 == 0 else None
            for c_idx, val in enumerate([m, f_val, inv_val, d_val], start=1):
                cell = ws.cell(row=curr_row, column=c_idx, value=val)
                cell.font = font_regular
                cell.border = border_all
                if row_fill:
                    cell.fill = row_fill
                cell.alignment = Alignment(horizontal="left" if c_idx == 1 else "right", vertical="center")

        kpi_start = table_start + len(table_rows) + 2
        for s_idx, (kpi_label, kpi_val) in enumerate(summary_rows, start=kpi_start):
            ws.row_dimensions[s_idx].height = 22
            c1 = ws.cell(row=s_idx, column=1, value=kpi_label)
            c1.font = font_bold
            c1.fill = highlight_fill
            c1.border = border_all
            c2 = ws.cell(row=s_idx, column=2, value=kpi_val)
            c2.font = font_bold
            c2.fill = highlight_fill
            c2.border = border_all
            ws.merge_cells(start_row=s_idx, start_column=2, end_row=s_idx, end_column=4)

        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 16)

        wb.save(excel_path)
    except Exception:
        logger.exception("Failed to append ROI sheet to Excel: %s", excel_path)
        raise


def append_roi_section_to_docx(doc, inp: dict, res: dict):
    try:
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        cur = inp.get("currency", "EGP")
        doc.add_page_break()
        h1 = doc.add_heading("Inverter vs Fixed Speed — ROI & Energy Analysis", level=1)
        h1.paragraph_format.space_before = Pt(8)
        h1.paragraph_format.space_after = Pt(6)

        intro = doc.add_paragraph(
            f"Financial and electrical feasibility assessment comparing standard Fixed-Speed AC "
            f"units against Variable-Speed Inverter technology for {inp['units']} unit(s) "
            f"with total load of {inp['btu']:,.0f} BTU/hr ({res['tons']:.2f} Tons)."
        )
        intro.runs[0].font.size = Pt(9.5)

        cols = ["Metric", "Fixed Speed", "Inverter", "Savings / Delta"]
        tbl = doc.add_table(rows=1, cols=4)
        tbl.style = "Light Grid Accent 1"
        hdr = tbl.rows[0].cells
        for i, name in enumerate(cols):
            hdr[i].text = name
            for p in hdr[i].paragraphs:
                for r in p.runs:
                    r.bold = True
                    r.font.size = Pt(9)

        pb_str = f"{res['payback_years']:.1f} Years" if res['payback_years'] is not None else "N/A"
        data_rows = [
            ("Power Input (kW)", f"{res['fixed_kw']:.2f} kW", f"{res['inverter_kw']:.2f} kW", f"-{res['fixed_kw'] - res['inverter_kw']:.2f} kW"),
            ("Annual Energy Consumption", f"{res['fixed_annual_kwh']:,.0f} kWh", f"{res['inverter_annual_kwh']:,.0f} kWh", f"{res['annual_kwh_saving']:,.0f} kWh/yr"),
            ("Annual Electricity Bill", f"{res['fixed_annual_cost']:,.0f} {cur}", f"{res['inverter_annual_cost']:,.0f} {cur}", f"{res['annual_saving']:,.0f} {cur}/yr"),
            ("Initial Equipment Cost", f"{res['fixed_upfront']:,.0f} {cur}", f"{res['inverter_upfront']:,.0f} {cur}", f"+{res['price_premium']:,.0f} {cur}"),
            (f"{inp['lifetime_years']}-Year Lifetime Cost", f"{res['fixed_lifetime_cost']:,.0f} {cur}", f"{res['inverter_lifetime_cost']:,.0f} {cur}", f"{res['lifetime_saving']:,.0f} {cur}"),
            ("Payback Period", "—", "—", pb_str),
        ]

        for m, f_v, inv_v, d_v in data_rows:
            cells = tbl.add_row().cells
            cells[0].text = m
            cells[1].text = f_v
            cells[2].text = inv_v
            cells[3].text = d_v
            for c in cells:
                for p in c.paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(8.5)

        doc.add_paragraph()

        rec_box = doc.add_paragraph()
        rec_box.paragraph_format.space_before = Pt(4)
        run_rec = rec_box.add_run(f"Consultant Recommendation: {res['rec_text']}")
        run_rec.bold = True
        run_rec.font.size = Pt(10)
        run_rec.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    except Exception:
        logger.exception("Failed to append ROI section to Word document")
        raise


# ── PySide6 Interactive UI Widget ─────────────────────────────────────────────

class InverterROICalculator(QWidget):
    roi_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.recompute()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # ── Row 1: Inputs ────
        inputs_hbox = QHBoxLayout()
        inputs_hbox.setSpacing(10)

        # 1. Operating Conditions
        grp_cond = QGroupBox("🕒  Operating Conditions")
        cond_lyt = QGridLayout(grp_cond)
        cond_lyt.setSpacing(6)
        cond_lyt.setContentsMargins(8, 8, 8, 8)

        cond_lyt.addWidget(QLabel("Cooling Load (BTU/hr):"), 0, 0)
        self.inp_btu = QDoubleSpinBox()
        self.inp_btu.setRange(0, 5_000_000)
        self.inp_btu.setValue(36000)
        self.inp_btu.setSingleStep(6000)
        self.inp_btu.setDecimals(0)
        self.inp_btu.valueChanged.connect(self.recompute)
        cond_lyt.addWidget(self.inp_btu, 0, 1)

        cond_lyt.addWidget(QLabel("Units Count:"), 1, 0)
        self.spin_units = QSpinBox()
        self.spin_units.setRange(1, 100)
        self.spin_units.setValue(1)
        self.spin_units.valueChanged.connect(self.recompute)
        cond_lyt.addWidget(self.spin_units, 1, 1)

        cond_lyt.addWidget(QLabel("Hours / Day:"), 2, 0)
        self.spin_hours = QDoubleSpinBox()
        self.spin_hours.setRange(1.0, 24.0)
        self.spin_hours.setValue(8.0)
        self.spin_hours.setSingleStep(1.0)
        self.spin_hours.valueChanged.connect(self.recompute)
        cond_lyt.addWidget(self.spin_hours, 2, 1)

        cond_lyt.addWidget(QLabel("Days / Year:"), 3, 0)
        self.spin_days = QSpinBox()
        self.spin_days.setRange(1, 365)
        self.spin_days.setValue(180)
        self.spin_days.valueChanged.connect(self.recompute)
        cond_lyt.addWidget(self.spin_days, 3, 1)

        cond_lyt.addWidget(QLabel("Tariff & Currency:"), 4, 0)
        row_tariff = QHBoxLayout()
        row_tariff.setSpacing(4)
        self.inp_tariff = QDoubleSpinBox()
        self.inp_tariff.setRange(0.01, 100.0)
        self.inp_tariff.setValue(1.50)
        self.inp_tariff.setSingleStep(0.25)
        self.inp_tariff.setDecimals(2)
        self.inp_tariff.valueChanged.connect(self.recompute)
        row_tariff.addWidget(self.inp_tariff)

        self.combo_currency = QComboBox()
        self.combo_currency.addItems(["EGP", "USD", "SAR", "AED"])
        self.combo_currency.setFixedWidth(65)
        self.combo_currency.currentIndexChanged.connect(self.recompute)
        row_tariff.addWidget(self.combo_currency)
        cond_lyt.addLayout(row_tariff, 4, 1)
        inputs_hbox.addWidget(grp_cond)

        # 2. Equipment Costs
        grp_cost = QGroupBox("💰  Equipment Costs")
        cost_lyt = QGridLayout(grp_cost)
        cost_lyt.setSpacing(6)
        cost_lyt.setContentsMargins(8, 8, 8, 8)

        cost_lyt.addWidget(QLabel("Fixed Speed Price:"), 0, 0)
        self.inp_fixed_price = QDoubleSpinBox()
        self.inp_fixed_price.setRange(0, 1_000_000)
        self.inp_fixed_price.setValue(18000)
        self.inp_fixed_price.setSingleStep(1000)
        self.inp_fixed_price.setDecimals(0)
        self.inp_fixed_price.valueChanged.connect(self.recompute)
        cost_lyt.addWidget(self.inp_fixed_price, 0, 1)

        cost_lyt.addWidget(QLabel("Inverter Unit Price:"), 1, 0)
        self.inp_inv_price = QDoubleSpinBox()
        self.inp_inv_price.setRange(0, 1_000_000)
        self.inp_inv_price.setValue(26500)
        self.inp_inv_price.setSingleStep(1000)
        self.inp_inv_price.setDecimals(0)
        self.inp_inv_price.valueChanged.connect(self.recompute)
        cost_lyt.addWidget(self.inp_inv_price, 1, 1)

        cost_lyt.addWidget(QLabel("Install Delta (Total):"), 2, 0)
        self.inp_install_delta = QDoubleSpinBox()
        self.inp_install_delta.setRange(0, 100_000)
        self.inp_install_delta.setValue(0)
        self.inp_install_delta.setSingleStep(500)
        self.inp_install_delta.setDecimals(0)
        self.inp_install_delta.valueChanged.connect(self.recompute)
        cost_lyt.addWidget(self.inp_install_delta, 2, 1)

        cost_lyt.addWidget(QLabel("Price Premium:"), 3, 0)
        self.lbl_premium = QLabel("0 EGP")
        self.lbl_premium.setStyleSheet("color: #93c5fd; font-weight: bold;")
        cost_lyt.addWidget(self.lbl_premium, 3, 1)
        inputs_hbox.addWidget(grp_cost)

        # 3. Efficiency & Lifecycle
        grp_assump = QGroupBox("⚙️  Efficiency • Lifecycle")
        assump_lyt = QGridLayout(grp_assump)
        assump_lyt.setSpacing(6)
        assump_lyt.setContentsMargins(8, 8, 8, 8)

        assump_lyt.addWidget(QLabel("Energy Saving %:"), 0, 0)
        row_saving = QHBoxLayout()
        row_saving.setSpacing(4)
        self.slider_saving = QSlider(Qt.Horizontal)
        self.slider_saving.setRange(10, 50)
        self.slider_saving.setValue(30)
        self.slider_saving.valueChanged.connect(self._sync_slider_saving)
        row_saving.addWidget(self.slider_saving)

        self.lbl_saving_val = QLabel("30 %")
        self.lbl_saving_val.setFixedWidth(38)
        self.lbl_saving_val.setStyleSheet("color: #34d399; font-weight: bold;")
        row_saving.addWidget(self.lbl_saving_val)
        assump_lyt.addLayout(row_saving, 0, 1)

        assump_lyt.addWidget(QLabel("System Lifetime:"), 1, 0)
        self.spin_lifetime = QSpinBox()
        self.spin_lifetime.setRange(5, 25)
        self.spin_lifetime.setValue(12)
        self.spin_lifetime.setSuffix(" yrs")
        self.spin_lifetime.valueChanged.connect(self.recompute)
        assump_lyt.addWidget(self.spin_lifetime, 1, 1)

        assump_lyt.addWidget(QLabel("CO₂ Factor (kg/kWh):"), 2, 0)
        self.inp_co2_factor = QDoubleSpinBox()
        self.inp_co2_factor.setRange(0.0, 2.0)
        self.inp_co2_factor.setValue(0.30)
        self.inp_co2_factor.setSingleStep(0.05)
        self.inp_co2_factor.setDecimals(2)
        self.inp_co2_factor.valueChanged.connect(self.recompute)
        assump_lyt.addWidget(self.inp_co2_factor, 2, 1)
        inputs_hbox.addWidget(grp_assump)

        root.addLayout(inputs_hbox)

        # ── Row 2: Results Table (All 5 rows visible) ────
        grp_res = QGroupBox("📊  Comparative Economic Results")
        res_lyt = QVBoxLayout(grp_res)
        res_lyt.setContentsMargins(8, 8, 8, 8)
        res_lyt.setSpacing(6)

        self.table_results = QTableWidget()
        self.table_results.setRowCount(5)
        self.table_results.setColumnCount(4)
        self.table_results.setHorizontalHeaderLabels(["Metric", "Fixed Speed", "Inverter", "Delta / Savings"])
        self.table_results.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_results.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_results.verticalHeader().setVisible(False)
        self.table_results.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_results.setAlternatingRowColors(True)
        self.table_results.setMinimumHeight(150)
        res_lyt.addWidget(self.table_results)

        # ── Row 3: KPI Cards ────
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)

        self.card_saving = self._build_kpi_card("💰 Annual Saving", "0 EGP/yr", "#34d399")
        self.card_payback = self._build_kpi_card("⏱ Payback Period", "0 Years", "#60a5fa")
        self.card_lifetime = self._build_kpi_card("📈 Lifetime Saving", "0 EGP", "#a78bfa")
        self.card_co2 = self._build_kpi_card("🌱 CO₂ Reduction", "0 kg/yr", "#38bdf8")

        kpi_row.addWidget(self.card_saving)
        kpi_row.addWidget(self.card_payback)
        kpi_row.addWidget(self.card_lifetime)
        kpi_row.addWidget(self.card_co2)
        res_lyt.addLayout(kpi_row)

        # Recommendation Banner
        self.lbl_recommendation = QLabel("Recommendation...")
        self.lbl_recommendation.setAlignment(Qt.AlignCenter)
        self.lbl_recommendation.setStyleSheet(
            "padding: 6px; font-weight: bold; font-size: 12px; "
            "border: 1px solid #374151; border-radius: 6px; background-color: #111827;"
        )
        res_lyt.addWidget(self.lbl_recommendation)
        root.addWidget(grp_res)

        # ── Row 4: Bottom Controls ────
        bot_row = QHBoxLayout()
        bot_row.setSpacing(10)

        self.chk_excel = QCheckBox("Include in Excel exports")
        self.chk_excel.setChecked(True)
        bot_row.addWidget(self.chk_excel)

        self.chk_word = QCheckBox("Include in Word reports")
        self.chk_word.setChecked(True)
        bot_row.addWidget(self.chk_word)

        bot_row.addStretch()

        btn_copy = QPushButton("📋  Copy (Markdown)")
        btn_copy.setObjectName("btn_pick")
        btn_copy.clicked.connect(self._copy_results)
        bot_row.addWidget(btn_copy)

        btn_export = QPushButton("💾  Export ROI Sheet")
        btn_export.setObjectName("btn_success")
        btn_export.clicked.connect(self._export_standalone_roi)
        bot_row.addWidget(btn_export)

        btn_reset = QPushButton("🔄  Reset")
        btn_reset.setObjectName("btn_danger")
        btn_reset.clicked.connect(self.reset_defaults)
        bot_row.addWidget(btn_reset)

        root.addLayout(bot_row)

    def _build_kpi_card(self, title: str, default_val: str, color_hex: str) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background-color: #111827; border: 1px solid #374151; border-radius: 6px;")
        lyt = QVBoxLayout(w)
        lyt.setContentsMargins(6, 4, 6, 4)
        lyt.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("color: #9ca3af; font-size: 11px;")
        lbl_t.setAlignment(Qt.AlignCenter)
        lyt.addWidget(lbl_t)

        lbl_v = QLabel(default_val)
        lbl_v.setObjectName("val")
        lbl_v.setStyleSheet(f"color: {color_hex}; font-size: 14px; font-weight: bold;")
        lbl_v.setAlignment(Qt.AlignCenter)
        lyt.addWidget(lbl_v)
        return w

    def _sync_slider_saving(self, val: int):
        self.lbl_saving_val.setText(f"{val} %")
        self.recompute()

    def set_capacity_from_load(self, btu: float, units: int = 1):
        if btu > 0:
            self.inp_btu.setValue(btu)
        if units > 0:
            self.spin_units.setValue(units)

    def get_inputs(self) -> dict:
        return {
            "btu": self.inp_btu.value(),
            "units": self.spin_units.value(),
            "hours_per_day": self.spin_hours.value(),
            "days_per_year": self.spin_days.value(),
            "tariff": self.inp_tariff.value(),
            "currency": self.combo_currency.currentText(),
            "fixed_unit_price": self.inp_fixed_price.value(),
            "inv_unit_price": self.inp_inv_price.value(),
            "install_delta": self.inp_install_delta.value(),
            "saving_pct": float(self.slider_saving.value()),
            "lifetime_years": self.spin_lifetime.value(),
            "co2_factor": self.inp_co2_factor.value(),
        }

    def is_excel_export_enabled(self) -> bool:
        return self.chk_excel.isChecked()

    def is_word_export_enabled(self) -> bool:
        return self.chk_word.isChecked()

    def recompute(self):
        inp = self.get_inputs()
        res = compute_roi(**{k: v for k, v in inp.items() if k != "currency"})
        cur = inp["currency"]

        self.lbl_premium.setText(f"{res['price_premium']:,.0f} {cur}")

        rows = [
            ("Electrical Input Power", f"{res['fixed_kw']:.2f} kW", f"{res['inverter_kw']:.2f} kW", f"-{res['fixed_kw'] - res['inverter_kw']:.2f} kW"),
            ("Annual Energy Consumption", f"{res['fixed_annual_kwh']:,.0f} kWh", f"{res['inverter_annual_kwh']:,.0f} kWh", f"-{res['annual_kwh_saving']:,.0f} kWh/yr"),
            ("Annual Electricity Bill", f"{res['fixed_annual_cost']:,.0f} {cur}", f"{res['inverter_annual_cost']:,.0f} {cur}", f"-{res['annual_saving']:,.0f} {cur}/yr"),
            ("Initial Equipment Cost", f"{res['fixed_upfront']:,.0f} {cur}", f"{res['inverter_upfront']:,.0f} {cur}", f"+{res['price_premium']:,.0f} {cur}"),
            (f"{inp['lifetime_years']}-Year Lifecycle Cost", f"{res['fixed_lifetime_cost']:,.0f} {cur}", f"{res['inverter_lifetime_cost']:,.0f} {cur}", f"-{res['lifetime_saving']:,.0f} {cur}"),
        ]
        for r_i, (metric, f_val, inv_val, d_val) in enumerate(rows):
            for c_i, val in enumerate([metric, f_val, inv_val, d_val]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter if c_i > 0 else (Qt.AlignLeft | Qt.AlignVCenter))
                if c_i == 3:
                    item.setForeground(QBrush(QColor("#34d399" if "-" in val else "#f87171")))
                self.table_results.setItem(r_i, c_i, item)

        self.card_saving.findChild(QLabel, "val").setText(f"{res['annual_saving']:,.0f} {cur}/yr")
        pb_str = f"{res['payback_years']:.1f} Years" if res['payback_years'] is not None else "N/A"
        self.card_payback.findChild(QLabel, "val").setText(pb_str)
        self.card_lifetime.findChild(QLabel, "val").setText(f"{res['lifetime_saving']:,.0f} {cur}")
        self.card_co2.findChild(QLabel, "val").setText(f"{res['annual_co2_kg']:,.0f} kg/yr")

        self.lbl_recommendation.setText(res['rec_text'])
        self.lbl_recommendation.setStyleSheet(
            f"padding: 6px; font-weight: bold; font-size: 12px; "
            f"border: 1px solid {res['rec_color']}; border-radius: 6px; "
            f"background-color: #111827; color: {res['rec_color']};"
        )

        self.roi_changed.emit({"inputs": inp, "results": res})

    def _copy_results(self):
        inp = self.get_inputs()
        res = compute_roi(**{k: v for k, v in inp.items() if k != "currency"})
        md = format_roi_markdown(inp, res)
        QApplication.clipboard().setText(md)
        QMessageBox.information(self, "Copied", "Markdown table copied to clipboard!")

    def _export_standalone_roi(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save ROI Workbook", "Inverter_ROI_Analysis.xlsx", "Excel Workbook (*.xlsx)")
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            inp = self.get_inputs()
            res = compute_roi(**{k: v for k, v in inp.items() if k != "currency"})
            with _atomic_export(path) as temporary_path:
                pd.DataFrame({"ROI Report": ["Generated by Carrier AC Selector"]}).to_excel(
                    temporary_path, index=False
                )
                append_roi_sheet_to_excel(temporary_path, inp, res)
        except Exception as exc:
            logger.exception("Standalone ROI export failed: %s", path)
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        QMessageBox.information(self, "Saved", f"ROI sheet saved successfully to:\n{path}")

    def reset_defaults(self):
        self.inp_btu.setValue(36000)
        self.spin_units.setValue(1)
        self.spin_hours.setValue(8.0)
        self.spin_days.setValue(180)
        self.inp_tariff.setValue(1.50)
        self.combo_currency.setCurrentText("EGP")
        self.inp_fixed_price.setValue(18000)
        self.inp_inv_price.setValue(26500)
        self.inp_install_delta.setValue(0)
        self.slider_saving.setValue(30)
        self.spin_lifetime.setValue(12)
        self.inp_co2_factor.setValue(0.30)
        self.recompute()
