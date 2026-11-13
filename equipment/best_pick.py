"""Best-unit picking over a results table (pure pandas, no Qt).

``best_overall_index`` is moved verbatim from ``HAPToCarrierApp``; ``pick_best``
holds the criterion rules that used to be interleaved with message boxes.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def best_overall_index(df):
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


@dataclass(frozen=True)
class BestPick:
    """Outcome of ``pick_best``: either a row index + reason, or a user notice."""
    index: object = None
    why: str = ""
    notice_title: str = ""
    notice_text: str = ""

    @property
    def ok(self) -> bool:
        return self.index is not None


def pick_best(df, criterion: str) -> BestPick:
    """Choose the best row of ``df`` for ``criterion`` (size / eer / cfm / overall)."""
    if criterion == "size":
        idx = df["Excess %"].idxmin()
        why = f"least oversized ({df.loc[idx, 'Excess %']:.1f}% excess)"
    elif criterion == "eer":
        valid = df[df["EER (Btu/Wh)"].notna()]
        if valid.empty:
            return BestPick(notice_title="No EER data",
                            notice_text="No EER values available.")
        idx = valid["EER (Btu/Wh)"].idxmax()
        why = f"highest EER ({valid.loc[idx, 'EER (Btu/Wh)']:g})"
    elif criterion == "cfm":
        cov = df["CFM Coverage %"]
        valid = df[cov.notna() & (cov >= 100)]
        if valid.empty:
            valid = df[cov.notna()]
        if valid.empty:
            return BestPick(notice_title="No CFM data",
                            notice_text="No CFM coverage values available.")
        idx = (valid["CFM Coverage %"] - 100).abs().idxmin()
        why = f"CFM coverage {valid.loc[idx, 'CFM Coverage %']:.0f}%"
    else:  # "overall"
        idx = best_overall_index(df)
        why = "balanced score (size + EER + airflow)"
    return BestPick(index=idx, why=why)
