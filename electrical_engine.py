"""
Electrical Load Schedule Engine
===============================
Pure Python electrical calculations for HVAC installations:
- FLA (Full Load Amperes) for 1Ph & 3Ph.
- MCB sizing based on standard IEC breaker ratings.
- Cable sizing with IEC 60364-5-52 derating factors (Ambient temp, Method, Grouping).
- Voltage drop calculation & automatic cable up-sizing if drop > 3.0%.
- Main feeder & panel sizing, Phase balancing check, Generator & UPS kVA sizing.
"""
from __future__ import annotations

import math
import re

# Standard Breaker Sizes (Amperes)
STANDARD_MCB_SIZES = [
    6, 10, 16, 20, 25, 32, 40, 50, 63, 80, 100,
    125, 160, 200, 250, 315, 400, 500, 630, 800
]

# Standard Cable Sizes (mm²)
STANDARD_CABLE_SIZES = [
    1.5, 2.5, 4, 6, 10, 16, 25, 35, 50, 70, 95, 120, 150, 185, 240, 300, 400
]

# Base Ampacity table for Copper (Cu) XLPE/PVC insulation at 30°C
# Format: size_mm2: (In Conduit / Trunking, In Perforated Cable Tray, Direct Buried)
CU_AMPACITY = {
    1.5:  (17.5, 22.0, 24.0),
    2.5:  (24.0, 30.0, 33.0),
    4.0:  (32.0, 40.0, 44.0),
    6.0:  (41.0, 51.0, 56.0),
    10.0: (57.0, 70.0, 76.0),
    16.0: (76.0, 94.0, 101.0),
    25.0: (101.0, 119.0, 130.0),
    35.0: (125.0, 148.0, 160.0),
    50.0: (151.0, 180.0, 195.0),
    70.0: (192.0, 232.0, 250.0),
    95.0: (232.0, 282.0, 305.0),
    120.0: (269.0, 328.0, 355.0),
    150.0: (300.0, 370.0, 400.0),
    185.0: (341.0, 424.0, 460.0),
    240.0: (400.0, 500.0, 545.0),
    300.0: (458.0, 576.0, 625.0),
    400.0: (525.0, 665.0, 720.0),
}

# Copper electrical properties (Resistance ohm/km at 50°C, Reactance ohm/km)
CU_IMPEDANCE = {
    1.5: (14.8, 0.098),
    2.5: (8.87, 0.091),
    4.0: (5.52, 0.086),
    6.0: (3.69, 0.082),
    10.0: (2.19, 0.078),
    16.0: (1.38, 0.075),
    25.0: (0.870, 0.073),
    35.0: (0.627, 0.072),
    50.0: (0.493, 0.071),
    70.0: (0.342, 0.070),
    95.0: (0.247, 0.069),
    120.0: (0.196, 0.068),
    150.0: (0.159, 0.068),
    185.0: (0.128, 0.067),
    240.0: (0.098, 0.066),
    300.0: (0.079, 0.065),
    400.0: (0.063, 0.064),
}

# IEC 60364-5-52 grouping factor for bunched circuits (approximate, Table B.52.17).
GROUPING_FACTORS = {
    1: 1.00, 2: 0.80, 3: 0.70, 4: 0.65, 5: 0.60,
    6: 0.57, 7: 0.54, 8: 0.52, 9: 0.50,
}


def get_grouping_derating(n_circuits: int) -> float:
    """Grouping factor for `n_circuits` bunched together (IEC 60364-5-52).

    1 circuit -> 1.00,  2 -> 0.80,  3 -> 0.70,  ... 9+ -> 0.50.
    """
    if n_circuits is None or n_circuits <= 1:
        return 1.00
    n = min(max(int(n_circuits), 1), 9)
    return GROUPING_FACTORS.get(n, 0.50)


def _ecc_size_for(phase_cable: float) -> float:
    """Earth conductor per IEC 60364-5-54 — round UP to next standard size."""
    if phase_cable <= 16:
        return phase_cable
    if phase_cable <= 35:
        return 16.0
    target = phase_cable / 2.0
    return next((s for s in STANDARD_CABLE_SIZES if s >= target),
                STANDARD_CABLE_SIZES[-1])


def parse_power_supply(ps_str: str) -> tuple[float, int]:
    """Parse '220-240V / 1Ph / 50Hz' or '380V / 3Ph' into (Voltage, Phase)."""
    s = str(ps_str or "").upper()
    phase = 3 if ("3PH" in s or "3-PH" in s or "3 PH" in s or "380" in s or "415" in s) else 1
    m_v = re.search(r"(\d{3})", s)
    if m_v:
        voltage = float(m_v.group(1))
    else:
        voltage = 380.0 if phase == 3 else 220.0
    return voltage, phase


def get_temperature_derating(temp_c: float) -> float:
    """Ambient temperature correction factor (ref 30°C for 70°C PVC)."""
    if temp_c <= 30.0:
        return 1.00
    if temp_c >= 65.0:
        return 0.35
    # Standard formula: sqrt((70 - Tamb) / (70 - 30))
    return round(math.sqrt((70.0 - temp_c) / 40.0), 3)


def calculate_unit_electrical(
    cooling_btu: float,
    cooling_kw: float,
    eer: float,
    power_supply_str: str,
    distance_m: float = 20.0,
    power_factor: float = 0.85,
    ambient_temp_c: float = 40.0,
    install_method: str = "Conduit",
    conductor_material: str = "Cu",
    grouped_circuits: int = 1,
    voltage_drop_limit_pct: float = 3.0,
    power_w_catalog: float | None = None,
    current_a_catalog: float | None = None,
) -> dict:
    """Calculate FLA, Breaker, Cable mm2, and Voltage Drop for a single HVAC unit."""
    # Fail loudly on NaN — a silent NaN used to become 0.5 kW in the max() below.
    for _name, _v in (("cooling_btu", cooling_btu), ("cooling_kw", cooling_kw),
                      ("eer", eer), ("distance_m", distance_m),
                      ("power_factor", power_factor),
                      ("ambient_temp_c", ambient_temp_c)):
        if isinstance(_v, float) and math.isnan(_v):
            raise ValueError(
                f"calculate_unit_electrical: {_name} is NaN — pass None "
                f"or a real number (use core.to_float at the boundary)."
            )
    if conductor_material != "Cu":
        raise NotImplementedError(
            f"Conductor material {conductor_material!r} is not implemented. "
            "Only copper ('Cu') ampacity/impedance tables are available — "
            "using aluminium would silently under-size the cable."
        )

    voltage, phase = parse_power_supply(power_supply_str)

    # 1. Electrical Input Power (kW_elec)
    if power_w_catalog is not None and power_w_catalog > 0:
        kw_elec = power_w_catalog / 1000.0
    elif eer and eer > 0:
        kw_elec = cooling_btu / (eer * 1000.0) if cooling_btu > 0 else (cooling_kw / 3.0)
    elif cooling_kw and cooling_kw > 0:
        kw_elec = cooling_kw / 3.0  # nominal COP = 3.0
    else:
        kw_elec = (cooling_btu / 12000.0) * 1.20 if cooling_btu > 0 else 1.5

    kw_elec = max(0.5, kw_elec)

    # 2. Full Load Amps (FLA)
    if current_a_catalog is not None and current_a_catalog > 0:
        fla = float(current_a_catalog)
    elif phase == 3:
        fla = (kw_elec * 1000.0) / (math.sqrt(3) * voltage * power_factor)
    else:
        fla = (kw_elec * 1000.0) / (voltage * power_factor)

    # 3. Breaker Sizing (MCB): Next standard size >= 1.25 * FLA
    mcb_target = fla * 1.25
    mcb_size = next((s for s in STANDARD_MCB_SIZES if s >= mcb_target), STANDARD_MCB_SIZES[-1])

    # 4. Cable Derating & Sizing (IEC 60364-5-52)
    k_temp = get_temperature_derating(ambient_temp_c)
    k_group = get_grouping_derating(grouped_circuits)
    method_idx = 0 if install_method == "Conduit" else (1 if install_method == "Tray" else 2)
    # Target continuous current after thermal AND grouping derating
    i_required = mcb_size / (max(0.4, k_temp) * k_group)

    # Find minimum cable size carrying i_required
    chosen_cable = STANDARD_CABLE_SIZES[-1]
    for size in STANDARD_CABLE_SIZES:
        base_amp = CU_AMPACITY.get(size, (0, 0, 0))[method_idx]
        if base_amp >= i_required:
            chosen_cable = size
            break

    # 5. Voltage Drop Verification & Auto-Upsizing
    r_val, x_val = CU_IMPEDANCE.get(chosen_cable, (0.1, 0.07))
    sin_phi = math.sqrt(max(0.0, 1.0 - (power_factor ** 2)))

    def compute_vdrop(c_size: float) -> tuple[float, float]:
        r, x = CU_IMPEDANCE.get(c_size, (0.1, 0.07))
        impedance_term = (r * power_factor) + (x * sin_phi)
        if phase == 3:
            dv = (math.sqrt(3) * fla * distance_m * impedance_term) / 1000.0
        else:
            dv = (2.0 * fla * distance_m * impedance_term) / 1000.0
        v_pct = (dv / voltage) * 100.0
        return dv, v_pct

    delta_v, vdrop_pct = compute_vdrop(chosen_cable)

    # Step up cable size if voltage drop exceeds the limit (e.g. 3%)
    while vdrop_pct > voltage_drop_limit_pct and chosen_cable < STANDARD_CABLE_SIZES[-1]:
        next_idx = STANDARD_CABLE_SIZES.index(chosen_cable) + 1
        if next_idx >= len(STANDARD_CABLE_SIZES):
            break
        chosen_cable = STANDARD_CABLE_SIZES[next_idx]
        delta_v, vdrop_pct = compute_vdrop(chosen_cable)

    # Earth Cable (ECC) sizing — always round UP to a standard size.
    ecc_cable = _ecc_size_for(chosen_cable)

    cable_desc = f"3×{chosen_cable:g} mm² + {ecc_cable:g} mm² E" if phase == 1 else f"4×{chosen_cable:g} mm² + {ecc_cable:g} mm² E"

    return {
        "kw_elec": kw_elec,
        "voltage": voltage,
        "phase": phase,
        "fla": fla,
        "mcb_size": mcb_size,
        "cable_size": chosen_cable,
        "ecc_size": ecc_cable,
        "cable_desc": cable_desc,
        "delta_v": delta_v,
        "vdrop_pct": vdrop_pct,
        "vdrop_ok": vdrop_pct <= voltage_drop_limit_pct,
    }


def compute_schedule_summary(
    unit_rows: list[dict],
    diversity_factor: float = 0.90,
    power_factor: float = 0.85,
    distance_to_main_m: float = 25.0,
    ambient_temp_c: float = 40.0,
) -> dict:
    """Compute building panel summary, feeder sizing, and generator/UPS requirements."""
    if not unit_rows:
        return {
            "total_kw": 0.0,
            "total_fla": 0.0,
            "demand_fla": 0.0,
            "is_three_phase_panel": False,
            "has_mixed_phases": False,
            "main_mcb": 0,
            "main_cable": 0,
            "main_cable_desc": "—",
            "main_vdrop_pct": 0.0,
            "generator_kva": 0.0,
            "ups_kva": 0.0,
            "panel_ways": 6,
            "units_count": 0,
            "warnings": [],
        }

    total_kw = sum(r["kw_elec"] * r.get("qty", 1) for r in unit_rows)
    phases = {r["phase"] for r in unit_rows}
    has_mixed_phases = len(phases) > 1
    is_three_phase_panel = (3 in phases) or has_mixed_phases

    # Total & Demand Currents
    if is_three_phase_panel:
        nominal_v = 380.0
        total_fla = (total_kw * 1000.0) / (math.sqrt(3) * nominal_v * power_factor)
    else:
        nominal_v = 220.0
        total_fla = (total_kw * 1000.0) / (nominal_v * power_factor)

    demand_fla = total_fla * diversity_factor

    # Main Breaker Sizing
    main_mcb_target = demand_fla * 1.25
    main_mcb = next((s for s in STANDARD_MCB_SIZES if s >= main_mcb_target), STANDARD_MCB_SIZES[-1])

    # Main Cable Sizing
    k_temp = get_temperature_derating(ambient_temp_c)
    i_feeder_req = main_mcb / max(0.4, k_temp)

    chosen_main_cable = STANDARD_CABLE_SIZES[-1]
    for size in STANDARD_CABLE_SIZES:
        base_amp = CU_AMPACITY.get(size, (0, 0, 0))[1]  # Tray default for main feeder
        if base_amp >= i_feeder_req:
            chosen_main_cable = size
            break

    # Main Feeder Voltage Drop
    r_val, x_val = CU_IMPEDANCE.get(chosen_main_cable, (0.1, 0.07))
    sin_phi = math.sqrt(max(0.0, 1.0 - (power_factor ** 2)))
    z_term = (r_val * power_factor) + (x_val * sin_phi)
    if is_three_phase_panel:
        dv_main = (math.sqrt(3) * demand_fla * distance_to_main_m * z_term) / 1000.0
    else:
        dv_main = (2.0 * demand_fla * distance_to_main_m * z_term) / 1000.0
    main_vdrop_pct = (dv_main / nominal_v) * 100.0

    # Generator sizing: Total kW * 1.25 motor starting factor / 0.85 PF
    generator_kva = round((total_kw * 1.25) / power_factor, 1)
    ups_kva = round(total_kw / 0.80, 1)

    # Panel Enclosure Sizing (Number of Ways)
    total_poles = sum((3 if r["phase"] == 3 else 1) * r.get("qty", 1) for r in unit_rows)
    panel_ways = math.ceil((total_poles + 4) / 6.0) * 6  # standard 12/18/24/36/48-way

    # Warnings & Recommendations
    warnings = []
    if has_mixed_phases:
        warnings.append(
            "⚠️ Mixed 1-Phase & 3-Phase loads detected — 3-Phase Distribution Panel "
            "required with balanced phase allocation (L1, L2, L3)."
        )
    if main_vdrop_pct > 2.0:
        warnings.append(
            f"⚠️ Main feeder voltage drop is {main_vdrop_pct:.1f}% (> 2.0% sub-main limit). "
            "Consider stepping up main feeder cross-section."
        )

    _ecc_main = max(_ecc_size_for(chosen_main_cable),
                    16.0 if is_three_phase_panel else 10.0)
    main_cable_desc = (
        f"4×{chosen_main_cable:g} mm² + {_ecc_main:g} mm² E"
        if is_three_phase_panel else
        f"2×{chosen_main_cable:g} mm² + {_ecc_main:g} mm² E"
    )

    return {
        "total_kw": total_kw,
        "total_fla": total_fla,
        "demand_fla": demand_fla,
        "is_three_phase_panel": is_three_phase_panel,
        "has_mixed_phases": has_mixed_phases,
        "main_mcb": main_mcb,
        "main_cable": chosen_main_cable,
        "main_cable_desc": main_cable_desc,
        "main_vdrop_pct": main_vdrop_pct,
        "generator_kva": generator_kva,
        "ups_kva": ups_kva,
        "panel_ways": panel_ways,
        "units_count": sum(r.get("qty", 1) for r in unit_rows),
        "warnings": warnings,
    }


def format_electrical_markdown(unit_rows: list[dict], summary: dict) -> str:
    """Generate Markdown representation of the Electrical Load Schedule."""
    lines = [
        "### 🔌 Electrical Load Schedule & Distribution",
        f"- **Total Connected Load**: {summary['total_kw']:.2f} kW | **Total FLA**: {summary['total_fla']:.1f} A",
        f"- **Design Demand Load**: {summary['demand_fla']:.1f} A (Diversity applied)",
        f"- **Main Incoming Breaker**: {summary['main_mcb']} A ({'3-Phase' if summary['is_three_phase_panel'] else '1-Phase'})",
        f"- **Main Feeder Cable**: {summary['main_cable_desc']} (V-Drop: {summary['main_vdrop_pct']:.2f}%)",
        f"- **Recommended Standby Generator**: {summary['generator_kva']} kVA | **UPS**: {summary['ups_kva']} kVA",
        "",
        "| # | System Tag | Model | Qty | Power (kW) | Supply | FLA (A) | MCB | Cable Size | V-Drop |",
        "|---|---|---|:---:|:---:|:---:|:---:|:---:|---|:---:|",
    ]
    for i, r in enumerate(unit_rows, start=1):
        v_ok = "✅" if r["vdrop_ok"] else "⚠️"
        lines.append(
            f"| {i} | {r.get('system', 'AC-'+str(i))} | {r.get('model', 'N/A')} | {r.get('qty', 1)} | "
            f"{r['kw_elec']:.2f} | {int(r['voltage'])}V/{r['phase']}Ph | {r['fla']:.1f} A | {r['mcb_size']}A | "
            f"{r['cable_desc']} | {r['vdrop_pct']:.1f}% {v_ok} |"
        )
    if summary["warnings"]:
        lines.append("")
        lines.extend(f"- {w}" for w in summary["warnings"])
    return "\n".join(lines)
