"""builds.macro.instruments — the macro daily family's instrument registry.

One pydantic carrier per macro instrument: which CSV the downloader
writes (csv_stem — MUST match downloads/macro/gold_silver/_endpoints.py),
its identity fields (name / metal_type label code / unit / source) and
the metal_type SMALLINT it writes into stats.macro_identity.

Units are stored RAW as the source publishes them — no conversion
(data-surface domain): Au99.99 in CNY/克, Ag99.99 in CNY/kg, the COMEX
main-continuous series in USD/ozt.
"""
from __future__ import annotations

from pydantic import BaseModel

from _common.labels import MACRO_METAL_CODE


class MacroInstrument(BaseModel):
    """One macro daily instrument (identity fields + its source CSV)."""

    code: str
    name: str
    metal: str          # MACRO_METAL_CODE label ('gold' | 'silver')
    unit: str
    source: str
    csv_stem: str

    @property
    def metal_type(self) -> int:
        """The static.label_dim 'macro_metal' SMALLINT code."""
        return MACRO_METAL_CODE[self.metal]


INSTRUMENTS: tuple[MacroInstrument, ...] = (
    MacroInstrument(
        code="Au99.99", name="上海金99.99", metal="gold",
        unit="CNY/克", source="sge", csv_stem="sge_au99.99",
    ),
    MacroInstrument(
        code="Ag99.99", name="上海银99.99", metal="silver",
        unit="CNY/kg", source="sge", csv_stem="sge_ag99.99",
    ),
    MacroInstrument(
        code="GC", name="COMEX黄金主力连续", metal="gold",
        unit="USD/ozt", source="sina", csv_stem="comex_gc",
    ),
    MacroInstrument(
        code="SI", name="COMEX白银主力连续", metal="silver",
        unit="USD/ozt", source="sina", csv_stem="comex_si",
    ),
)

INSTRUMENT_BY_CODE: dict[str, MacroInstrument] = {
    inst.code: inst for inst in INSTRUMENTS
}
