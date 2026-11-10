# Engineering Calculation Limitations

## Electrical schedule

The electrical schedule is a preliminary sizing aid, not an approved installation design.

- When manufacturer current is absent, FLA is estimated from catalogue power, EER, or an assumed COP. The UI and results must retain this distinction.
- A generic 125% FLA breaker target is only a preliminary estimate. It does not replace manufacturer MCA/MOCP requirements or protection coordination.
- Catalogue ampacity tables are limited to the listed copper conductor sizes, installation methods, and correction-factor assumptions. If no listed size satisfies the corrected requirement, the result is explicitly unverified.
- Mixed single-phase and three-phase loads are not automatically phase-balanced. The panel current is a preliminary estimate pending L1/L2/L3 allocation and neutral-current assessment.
- Voltage-drop compliance does not prove thermal ampacity, short-circuit withstand, disconnection time, or code compliance.

Before construction, verify the equipment nameplate, MCA/MOCP, conductor insulation and installation conditions, ambient/grouping factors, protective-device characteristics, fault level, earthing, and applicable local regulations with the responsible electrical engineer.

## Inverter ROI

ROI outputs are scenario estimates driven by user-entered operating hours, days, tariff, and saving percentage. They are not measured energy performance or a manufacturer part-load simulation. Confirm assumptions against actual load profiles and product performance data before using the result for procurement.

## Capacity derating

Nominal catalogue capacity and effective capacity after the selected derating percentage are separate values. Selection, cart totals, and exports should use effective capacity for adequacy checks while retaining nominal capacity as catalogue data.
