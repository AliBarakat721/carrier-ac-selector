#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Carrier AC Selector — high-priority patch script
#
# Usage:
#   bash apply_high_priority_patches.sh              # uses current dir
#   bash apply_high_priority_patches.sh /path/to/proj
#
# Fixes:
#   core.py               to_float: decimal commas ('10,11' → 10.11)  [A]
#   main.py               batch miscount ('Not parsed' ≠ matched)     [#1]
#   main.py               Electrical sync_units NaN/text safe         [#2/C]
#   electrical_engine.py  NaN inputs raise (no silent 0.5 kW)         [C]
#   electrical_engine.py  conductor_material != 'Cu' raises           [B]
#   electrical_engine.py  grouping factor actually applied            [#3]
#   electrical_engine.py  ECC rounded UP to standard sizes            [#4]
# ─────────────────────────────────────────────────────────────────────────

PROJECT_DIR="${1:-$PWD}"
cd "$PROJECT_DIR" || { echo "Cannot cd to $PROJECT_DIR"; exit 1; }

G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; B=$'\033[34m'
BOLD=$'\033[1m'; N=$'\033[0m'

echo "${BOLD}${B}Carrier AC Selector — high-priority patch script${N}"
echo "Project: $PROJECT_DIR"
echo

# ── 1. Required files present? ──────────────────────────────────────────
missing=0
for f in core.py main.py electrical_engine.py; do
    if [ ! -f "$f" ]; then
        echo "${R}✘ Missing: $f${N}"
        missing=1
    fi
done
[ "$missing" -eq 0 ] || { echo "Aborting."; exit 1; }
echo "${G}✔ Target files present${N}"

# ── 2. Backup ───────────────────────────────────────────────────────────
BACKUP=".patch_backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP"
cp core.py main.py electrical_engine.py "$BACKUP/"
echo "${G}✔ Backup → ${BACKUP}/${N}"
echo

# ── 3. Apply patches ────────────────────────────────────────────────────
echo "${BOLD}${B}Applying patches…${N}"

python3 <<'PYEOF'
import io, sys

FAILED = []

def patch(path, label, old, new):
    with io.open(path, "r", encoding="utf-8") as f:
        src = f.read()
    n = src.count(old)
    if n == 0:
        print(f"  \033[31m✘ {label}\033[0m")
        print(f"      pattern not found in {path} (already applied?)")
        FAILED.append(label)
        return
    if n > 1:
        print(f"  \033[33m⚠ {label}\033[0m — found {n} matches (expected 1); skipped")
        FAILED.append(label)
        return
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new, 1))
    print(f"  \033[32m✔ {label}\033[0m")


# ═══════════════════════════════════════════════════════════════════════
#  core.py — to_float: handle decimal comma
# ═══════════════════════════════════════════════════════════════════════
patch("core.py", "core.to_float: decimal-comma handling",
'''def to_float(value):
    """Return value as float, or None for blanks / em-dash / unparsable.

    Tolerates the '≈' prefix used for approximate catalogue values.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    s = str(value).replace("≈", "").replace(",", "").strip()
    if s in _NUMERIC_NA:
        return None
    try:
        return float(s)
    except ValueError:
        return None''',
'''def to_float(value):
    """Return value as float, or None for blanks / em-dash / unparsable.

    Tolerates the '≈' prefix used for approximate catalogue values and
    distinguishes thousands separators from European decimal commas:
        '1,234.5' -> 1234.5    '1.234,5' -> 1234.5    '10,11' -> 10.11
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    s = str(value).replace("≈", "").strip()
    if s in _NUMERIC_NA:
        return None
    # Mirror parse_user_number's separator logic (decimal comma vs thousands).
    if ',' in s and '.' in s:
        if s.rfind('.') > s.rfind(','):
            s = s.replace(',', '')                     # 1,234.5 -> US
        else:
            s = s.replace('.', '').replace(',', '.')   # 1.234,5 -> EU
    elif ',' in s:
        if re.fullmatch(r'-?\\d{1,3}(,\\d{3})+', s):
            s = s.replace(',', '')                     # 1,234,567 -> thousands
        else:
            s = s.replace(',', '.')                    # 10,11 -> 10.11
    try:
        return float(s)
    except ValueError:
        return None''')


# ═══════════════════════════════════════════════════════════════════════
#  main.py — batch miscount: 'Not parsed' is not a match
# ═══════════════════════════════════════════════════════════════════════
patch("main.py", "main._render_batch_body: miscount fix",
'''        self.table_batch.setSortingEnabled(True)
        n_ok = int((batch['Status'] != 'No match').sum())''',
'''        self.table_batch.setSortingEnabled(True)
        n_ok = int((~batch['Status'].isin(['No match', 'Not parsed'])).sum())''')


# ═══════════════════════════════════════════════════════════════════════
#  main.py — ElectricalScheduleTab.sync_units: NaN/text-safe
# ═══════════════════════════════════════════════════════════════════════
patch("main.py", "main.ElectricalScheduleTab.sync_units: NaN-safe",
'''        if selected_dict:
            for sys_name, entry in selected_dict.items():
                r = entry["row"]
                p_w = r.get("Cooling Power Input (W)")
                c_a = r.get("Cooling Current (A)")
                power_w_val = float(p_w) if pd.notna(p_w) and str(p_w).strip() not in ("", "—", "-") else None
                curr_a_val = float(c_a) if pd.notna(c_a) and str(c_a).strip() not in ("", "—", "-") else None

                raw.append({
                    "system": sys_name,
                    "model": str(r.get("System Model", "N/A")),
                    "btu": float(r.get("Cooling Capacity (Btu/hr)", 0) or 0),
                    "kw": float(r.get("Cooling Capacity (kW)", 0) or 0),
                    "eer": float(r.get("EER (Btu/Wh)", 0) or 0) if pd.notna(r.get("EER (Btu/Wh)")) else 0.0,
                    "ps": str(r.get("Power Supply", "220V / 1Ph")),
                    "qty": int(entry.get("units", 1)),
                    "power_w": power_w_val,
                    "current_a": curr_a_val,
                })''',
'''        if selected_dict:
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
                })''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — module-level helpers (grouping + ECC)
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: add GROUPING_FACTORS + helpers",
'''    300.0: (0.079, 0.065),
    400.0: (0.063, 0.064),
}


def parse_power_supply(ps_str: str) -> tuple[float, int]:''',
'''    300.0: (0.079, 0.065),
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


def parse_power_supply(ps_str: str) -> tuple[float, int]:''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — calculate_unit_electrical signature: add grouped_circuits
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: add grouped_circuits param",
'''    ambient_temp_c: float = 40.0,
    install_method: str = "Conduit",
    conductor_material: str = "Cu",
    voltage_drop_limit_pct: float = 3.0,''',
'''    ambient_temp_c: float = 40.0,
    install_method: str = "Conduit",
    conductor_material: str = "Cu",
    grouped_circuits: int = 1,
    voltage_drop_limit_pct: float = 3.0,''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — NaN guard + conductor_material check
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: NaN guard + conductor check",
'''    """Calculate FLA, Breaker, Cable mm2, and Voltage Drop for a single HVAC unit."""
    voltage, phase = parse_power_supply(power_supply_str)''',
'''    """Calculate FLA, Breaker, Cable mm2, and Voltage Drop for a single HVAC unit."""
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

    voltage, phase = parse_power_supply(power_supply_str)''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — apply grouping factor in derating
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: apply grouping factor",
'''    # 4. Cable Derating & Sizing (IEC 60364-5-52)
    k_temp = get_temperature_derating(ambient_temp_c)
    method_idx = 0 if install_method == "Conduit" else (1 if install_method == "Tray" else 2)
    # Target continuous current after thermal derating
    i_required = mcb_size / max(0.4, k_temp)''',
'''    # 4. Cable Derating & Sizing (IEC 60364-5-52)
    k_temp = get_temperature_derating(ambient_temp_c)
    k_group = get_grouping_derating(grouped_circuits)
    method_idx = 0 if install_method == "Conduit" else (1 if install_method == "Tray" else 2)
    # Target continuous current after thermal AND grouping derating
    i_required = mcb_size / (max(0.4, k_temp) * k_group)''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — ECC sizing: round UP
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: ECC rounded up",
'''    # Earth Cable (ECC) sizing: <=16 -> same, 25-35 -> 16, >35 -> half
    if chosen_cable <= 16:
        ecc_cable = chosen_cable
    elif chosen_cable <= 35:
        ecc_cable = 16.0
    else:
        ecc_cable = chosen_cable / 2.0''',
'''    # Earth Cable (ECC) sizing — always round UP to a standard size.
    ecc_cable = _ecc_size_for(chosen_cable)''')


# ═══════════════════════════════════════════════════════════════════════
#  electrical_engine.py — main feeder ECC: round UP
# ═══════════════════════════════════════════════════════════════════════
patch("electrical_engine.py", "electrical_engine: main feeder ECC rounded up",
'''    main_cable_desc = (
        f"4×{chosen_main_cable:g} mm² + {max(16.0, chosen_main_cable/2.0):g} mm² E"
        if is_three_phase_panel else
        f"2×{chosen_main_cable:g} mm² + {max(10.0, chosen_main_cable/2.0):g} mm² E"
    )''',
'''    _ecc_main = max(_ecc_size_for(chosen_main_cable),
                    16.0 if is_three_phase_panel else 10.0)
    main_cable_desc = (
        f"4×{chosen_main_cable:g} mm² + {_ecc_main:g} mm² E"
        if is_three_phase_panel else
        f"2×{chosen_main_cable:g} mm² + {_ecc_main:g} mm² E"
    )''')


# ── Result ──────────────────────────────────────────────────────────────
if FAILED:
    print()
    print(f"\033[31m✘ {len(FAILED)} patch(es) failed:\033[0m")
    for f in FAILED:
        print(f"    • {f}")
    sys.exit(2)
PYEOF
PATCH_STATUS=$?

if [ $PATCH_STATUS -ne 0 ]; then
    echo
    echo "${R}✘ Patch script failed (exit $PATCH_STATUS).${N}"
    echo "   Restore originals with:"
    echo "     cp ${BACKUP}/*.py ."
    exit $PATCH_STATUS
fi

# ── 4. Compile check ────────────────────────────────────────────────────
echo
echo "${BOLD}${B}Verifying syntax…${N}"
if python3 -m py_compile core.py main.py electrical_engine.py; then
    echo "${G}✔ All files compile cleanly${N}"
else
    echo "${R}✘ Compile failed — restoring backup${N}"
    cp "$BACKUP"/*.py .
    exit 1
fi

# ── 5. Summary ──────────────────────────────────────────────────────────
echo
echo "${BOLD}${G}✅ All high-priority patches applied${N}"
echo
echo "   Backup:   ${BACKUP}/"
echo "   Restore:  cp ${BACKUP}/*.py ."
echo
echo "   Patches:"
echo "     • core.py               to_float: '10,11' → 10.11 (was 1011)"
echo "     • main.py               batch: 'Not parsed' no longer counted as matched"
echo "     • main.py               Electrical sync_units: NaN-safe via core.to_float"
echo "     • electrical_engine.py  NaN inputs raise (no silent 0.5 kW)"
echo "     • electrical_engine.py  conductor_material != 'Cu' raises clearly"
echo "     • electrical_engine.py  grouping factor now applied (IEC 60364-5-52)"
echo "     • electrical_engine.py  ECC rounded UP to standard sizes"
echo
echo "   Suggested manual checks:"
echo "     python3 -c \"import core; print(core.to_float('10,11'), core.to_float('1,234'), core.to_float('1.234,5'))\""
echo "       → expect  10.11 1234.0 1234.5"
echo "     python3 -c \"import electrical_engine as e; print(e.get_grouping_derating(4))\""
echo "       → expect  0.65"
echo
