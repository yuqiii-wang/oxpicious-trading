"""Extract target sections from a fund quarterly report PDF to CSV.

All six target sections are extracted together (both stock- and bond-based
sections) regardless of the fund type. Sections not present in the PDF
produce a 0-byte CSV placeholder, so every PDF always has the same set of
output CSVs. An additional identify CSV (key-value) records basic fund
metadata and which sub-sections of the 投资组合报告 had content.

Target sections (all always written):
  1. 报告期末按公允价值占基金资产净值比例大小排序的前十名股票投资明细
     — top-10 stock holdings (6 cols: 序号 股票代码 股票名称 数量 公允价值 占净值比)
  2. 报告期末按行业分类的境内股票投资组合
     — domestic stock portfolio by industry (4 cols: 代码 行业类别 公允价值 占净值比)
  3. 报告期末基金资产组合情况
     — fund asset portfolio (4 cols: 序号 项目 金额 占总资产比)
  4. 报告期末按债券品种分类的债券投资组合
     — bond portfolio by bond type (4 cols: 序号 债券品种 公允价值 占净值比)
  5. 报告期末投资组合平均剩余期限分布比例
     — average remaining maturity distribution
     (4 cols: 序号 平均剩余期限 各期限资产占净值比 各期限负债占净值比)
  6. 报告期末按(公允价值|摊余成本)占基金资产净值比例大小排名的前十名债券投资明细
     — top-10 bond holdings
     (6 cols: 序号 债券代码 债券名称 债券数量 公允价值 占净值比)
     The section title varies by fund type (公允价值 for fair-value funds,
     摊余成本 for amortized-cost / money-market funds); matching uses the
     stable keyword "前十名债券投资明细".

Tables frequently span a page boundary, so we scan every page, classify each
extracted table by its header row, and merge consecutive tables that share
the same header (dropping repeated header rows). Headerless continuation
tables are assigned to the most recently seen section with a matching column
count.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pdfplumber


# Section labels — matched as substrings of page text to locate each section,
# and used as the CSV basenames.
SECTION_TOP10 = "前十名股票投资明细"
SECTION_INDUSTRY = "按行业分类的境内股票投资组合"
SECTION_ASSET = "报告期末基金资产组合情况"

# Bond-specific sections.
SECTION_BOND_TYPE = "按债券品种分类的债券投资组合"
SECTION_REMAINING_MATURE = "投资组合平均剩余期限分布比例"
SECTION_TOP10_BONDS = "前十名债券投资明细"

# All target sections — always written (0-byte placeholder when not found).
SECTIONS: Tuple[str, ...] = (
    SECTION_ASSET, SECTION_INDUSTRY, SECTION_TOP10,
    SECTION_BOND_TYPE, SECTION_REMAINING_MATURE, SECTION_TOP10_BONDS,
)

# Human-readable labels for the identify CSV (投资组合报告 sub-sections).
SECTION_LABELS_CN: Dict[str, str] = {
    SECTION_TOP10: "前十名股票投资明细",
    SECTION_INDUSTRY: "按行业分类的境内股票投资组合",
    SECTION_ASSET: "报告期末基金资产组合情况",
    SECTION_BOND_TYPE: "按债券品种分类的债券投资组合",
    SECTION_REMAINING_MATURE: "投资组合平均剩余期限分布比例",
    SECTION_TOP10_BONDS: "前十名债券投资明细",
}

# Header signatures: a table is classified as a section if its first row
# (joined) contains ALL of the signature tokens. This is robust to slight
# whitespace / newline differences produced by pdfplumber.
HEADER_SIGNATURES: Dict[str, Tuple[str, ...]] = {
    SECTION_ASSET: ("序号", "项目", "金额", "占基金总资产"),
    SECTION_INDUSTRY: ("代码", "行业类别", "公允价值", "占基金资产净值"),
    SECTION_TOP10: ("序号", "股票代码", "股票名称", "公允价值", "占基金资产净值"),
    SECTION_BOND_TYPE: ("序号", "债券品种", "公允价值", "占基金资产净值"),
    SECTION_REMAINING_MATURE: (
        "序号", "平均剩余期限",
        "各期限资产占基金资产净值", "各期限负债占基金资产净值",
    ),
    SECTION_TOP10_BONDS: (
        "序号", "债券代码", "债券名称", "公允价值", "占基金资产净值",
    ),
}

# Expected column counts per section (for sanity-checking merged tables).
EXPECTED_COLS: Dict[str, int] = {
    SECTION_ASSET: 4,
    SECTION_INDUSTRY: 4,
    SECTION_TOP10: 6,
    SECTION_BOND_TYPE: 4,
    SECTION_REMAINING_MATURE: 4,
    SECTION_TOP10_BONDS: 6,
}

# CSV basenames written next to the source PDF.
CSV_NAMES: Dict[str, str] = {
    SECTION_TOP10: "top10_holdings.csv",
    SECTION_INDUSTRY: "industry_portfolio.csv",
    SECTION_ASSET: "asset_portfolio.csv",
    SECTION_BOND_TYPE: "bond_type_portfolio.csv",
    SECTION_REMAINING_MATURE: "remaining_maturity.csv",
    SECTION_TOP10_BONDS: "top10_bonds.csv",
}

# Key-value identify CSV (basic fund metadata + per-section content summary).
IDENTIFY_CSV_NAME = "identify.csv"


def _clean_cell(cell: Optional[str]) -> str:
    """Normalise a pdfplumber cell: None -> '', collapse internal newlines."""
    if cell is None:
        return ""
    return str(cell).replace("\n", "").replace("\r", "").strip()


def _compact_columns(rows: List[List[str]]) -> List[List[str]]:
    """Remove columns that are entirely empty across all rows.

    Older PDFs produce tables with many empty columns (artifacts of merged
    cells in the source document). Compacting them yields the true column
    count so downstream classification and CSV output are correct.
    """
    if not rows:
        return rows
    ncol = max(len(r) for r in rows)
    keep = []
    for ci in range(ncol):
        col = [r[ci] if ci < len(r) else "" for r in rows]
        if any(c.strip() for c in col):
            keep.append(ci)
    return [[r[ci] if ci < len(r) else "" for ci in keep] for r in rows]


def _compact_rows(rows: List[List[str]], expected_cols: int) -> List[List[str]]:
    """Per-row removal of empty cells for tables with a zigzag merge pattern.

    When _compact_columns still leaves more than ``expected_cols`` columns
    (because data and header tokens alternate in different columns — a
    common artifact of merged cells in older PDFs), we remove empty cells
    from each row individually and re-pad to ``expected_cols``.

    This is safe because the zigzag pattern only occurs in tables where every
    data cell is non-empty (top10 holdings: 序号, code, name, qty, value, ratio
    are all filled). It is NOT applied to tables that already have the right
    column count (asset/industry tables where sub-rows legitimately have empty
    first cells).
    """
    ncol = max(len(r) for r in rows) if rows else 0
    if ncol <= expected_cols:
        return rows
    out = []
    for r in rows:
        non_empty = [c for c in r if c.strip()]
        # Pad to expected_cols if short
        if len(non_empty) < expected_cols:
            non_empty = non_empty + [""] * (expected_cols - len(non_empty))
        out.append(non_empty[:expected_cols])
    return out


def _merge_split_header(rows: List[List[str]], max_header_rows: int = 3) -> List[List[str]]:
    """Merge a multi-row header into a single header row.

    Older PDFs split the header across 2-3 rows (e.g. "占基金资产净值" on row 0,
    "序号 股票代码 ..." on row 1, "例（%）" on row 2). We detect this by
    checking if the first *max_header_rows* rows contain only header fragments
    (no data-like cells), and merge them into a single header row.

    A row is considered a "header fragment" if it has at most 2 non-empty cells
    OR its non-empty cells are all header tokens (not digits / numbers).
    """
    if len(rows) <= 1:
        return rows
    header_fragments = []
    data_start = 0
    for i, row in enumerate(rows[:max_header_rows]):
        non_empty = [c for c in row if c.strip()]
        if len(non_empty) <= 2 or all(
            not c.strip().replace(",", "").replace(".", "").replace("-", "").isdigit()
            for c in non_empty
        ):
            header_fragments.append(row)
            data_start = i + 1
        else:
            break
    if data_start <= 1:
        return rows  # no split header detected
    # Merge fragments cell-by-cell (take the first non-empty value per column)
    merged = list(rows[0])
    for frag in header_fragments[1:]:
        for ci, val in enumerate(frag):
            if ci < len(merged) and not merged[ci].strip() and val.strip():
                merged[ci] = val
    return [merged] + rows[data_start:]


def _row_is_header(row: List[str], section: str) -> bool:
    """True if *row* looks like the section's header row."""
    joined = "".join(row)
    return all(tok in joined for tok in HEADER_SIGNATURES[section])


def _classify_table(table: List[List[Optional[str]]]) -> Optional[str]:
    """Return the section label if *table*'s header matches a known section.

    Handles both single-row headers (modern PDFs) and multi-row split headers
    (older PDFs with merged cells). Columns are compacted first to remove
    empty artifacts, then the first few rows are checked for signature tokens.
    """
    if not table or not table[0]:
        return None
    rows = [[_clean_cell(c) for c in row] for row in table if row]
    rows = _compact_columns(rows)
    rows = _merge_split_header(rows)
    # Check the merged header row (and optionally the first 2 rows as fallback)
    for check_row in rows[:2]:
        joined = "".join(check_row)
        for section, sigs in HEADER_SIGNATURES.items():
            if all(tok in joined for tok in sigs):
                return section
    return None


def _classify_by_content(table: List[List[Optional[str]]]) -> Optional[str]:
    """Classify a headerless continuation table by inspecting its data rows.

    Used when a table spans a page break and the continuation page's table
    has no header row. We look at the first cells of the data rows:
      - industry: first cell is a single uppercase letter A-Z (industry code),
                  possibly with a trailing 合计 row whose first cell is empty
      - top10 stocks: first cell is a digit 1-10
      - top10 bonds:  first cell is a digit 1-10 (6 cols, second cell is a
                      long numeric bond code)
      - bond type:    first cell is a digit, second cell is a bond-type label
                      (债券/票据/存单/融资券...) or 其中 sub-row
      - remaining maturity: first cell is a digit or 合计, second cell
                      contains "天" (a maturity bucket like "30天以内")
      - asset:        first cell is a digit or 合计 (second cell is a balance
                      sheet item, no "天")
    """
    if not table:
        return None
    rows = [[_clean_cell(c) for c in row] for row in table if row]
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    first_cells = [r[0].strip() if r else "" for r in rows]
    non_empty = [c for c in first_cells if c]
    if not non_empty:
        return None
    second_cells = [r[1].strip() if len(r) > 1 else "" for r in rows]

    # Industry: single uppercase letters (allow a trailing 合计 row whose
    # first cell may be empty — 合计 appears in the second column).
    if ncol == 4 and all(re.match(r"^[A-Z]$", c) for c in non_empty):
        return SECTION_INDUSTRY
    # Top10 stocks / bonds: digits 1-10. 6 cols each. Distinguish by the
    # second cell: stocks use a 6-digit A-share code, bonds use a longer
    # numeric code; but both are numeric, so we cannot reliably tell them
    # apart by content alone. Prefer bond classification only when a bond
    # keyword is present; otherwise default to stock. (In practice the
    # headerless top10 continuation is rare; header classification handles
    # the first occurrence and sets last_section for the continuation.)
    if ncol == 6 and all(c.isdigit() and 1 <= int(c) <= 10 for c in non_empty):
        joined_second = "".join(second_cells)
        if "债" in joined_second or "CD" in joined_second or "存单" in joined_second:
            return SECTION_TOP10_BONDS
        return SECTION_TOP10
    # 4-col tables with digit/合计 first cells: remaining maturity vs asset
    # vs bond-type. Distinguish by the second column.
    if ncol == 4 and all(c.isdigit() or c == "合计" for c in non_empty):
        if any("天" in c for c in second_cells):
            return SECTION_REMAINING_MATURE
        # Bond-type labels (国家债券/央行票据/金融债券/同业存单/融资券...).
        # Exclude cells mentioning 回购 (repo): the 报告期债券回购融资情况
        # table shares the 4-col digit shape but is not a target section.
        has_bond_kw = any(
            ("债券" in c or "票据" in c or "存单" in c or "融资券" in c)
            and "回购" not in c
            for c in second_cells
        )
        # 其中 sub-rows that belong to bond-type (e.g. 其中：政策性金融债).
        has_bond_sub = any(
            c.startswith("其中") and "回购" not in c
            and ("债" in c or "票据" in c or "存单" in c)
            for c in second_cells
        )
        if has_bond_kw or has_bond_sub:
            return SECTION_BOND_TYPE
        # Repo financing table (报告期内/末债券回购融资余额) — not a target.
        if any("回购" in c for c in second_cells):
            return None
        return SECTION_ASSET
    return None


def _extract_section_rows(
    pdf: pdfplumber.PDF,
) -> Dict[str, List[List[str]]]:
    """Scan every page, classify tables, and merge tables per section.

    Returns a dict {section_label: [header_row, data_row, ...]}.
    Tables are merged in page order; repeated header rows (from page breaks)
    are dropped so each section has exactly one header row at index 0.

    A table is classified first by its header row (_classify_table); if that
    fails (common for continuation tables on a new page that have no header),
    we try to assign it to the most recently seen section whose expected
    column count matches (cross-page continuation), and finally fall back to
    classifying by data-row content (_classify_by_content).

    A section is "closed" once a 合计 (total) row has been appended to it.
    Headerless continuation tables are not merged into a closed section —
    this prevents unrelated trailing tables (e.g. 报告期债券回购融资情况)
    from being absorbed into the already-complete asset portfolio.

    For tables with empty-column artifacts (older PDFs with merged cells),
    columns are compacted and multi-row split headers are merged before
    the rows are stored, so CSV output has the expected column count.
    """
    result: Dict[str, List[List[str]]] = {}
    last_section: Optional[str] = None
    closed: set = set()
    for page in pdf.pages:
        tables = page.extract_tables() or []
        for tbl in tables:
            section = _classify_table(tbl)
            via_header = section is not None
            if section is None:
                # Headerless table. Prefer the most recent section if it is
                # still open and the (compacted) column count matches.
                rows_tmp = _compact_columns(
                    [[_clean_cell(c) for c in row] for row in tbl if row]
                )
                ncol = max((len(r) for r in rows_tmp), default=0)
                if (last_section is not None
                        and last_section not in closed
                        and ncol == EXPECTED_COLS.get(last_section, -1)):
                    section = last_section
                else:
                    section = _classify_by_content(tbl)
            if section is None:
                continue
            # A content-classified table that maps to an already-closed
            # section is rejected (e.g. the 债券回购融资 table that follows
            # the asset portfolio's 合计 row).
            if not via_header and section in closed:
                continue
            rows = [[_clean_cell(c) for c in row] for row in tbl if row]
            if not rows:
                continue
            # Compact empty columns and merge split headers so the stored
            # rows have the true column count.
            rows = _compact_columns(rows)
            rows = _merge_split_header(rows)
            # Drop extra header-fragment rows that _merge_split_header may
            # have left behind (rows before the first data row that still
            # look like header fragments).
            while len(rows) > 1 and not any(
                c.strip().replace(",", "").replace(".", "").replace("-", "").isdigit()
                for c in rows[1] if c
            ) and not _row_is_header(rows[1], section) and _row_is_header(rows[0], section):
                # rows[0] is the merged header, rows[1] is another fragment — drop it
                rows.pop(1)
            # Per-row compaction for zigzag merge artifacts (older PDFs).
            want = EXPECTED_COLS[section]
            rows = _compact_rows(rows, want)
            # Normalise column count: pad / trim to EXPECTED_COLS so CSV rows
            # line up even when pdfplumber splits a cell.
            rows = [r[:want] + [""] * (want - len(r)) if len(r) < want else r[:want]
                    for r in rows]
            if section not in result:
                result[section] = rows
            else:
                existing = result[section]
                # Drop a repeated header row at the top of the continuation table.
                if _row_is_header(rows[0], section):
                    rows = rows[1:]
                existing.extend(rows)
            last_section = section
            # Close the section once its 合计 (total) row has been seen.
            if any(c.strip() == "合计" for r in result[section] for c in r):
                closed.add(section)
    return result


def _filter_industry_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the industry table, drop trailing non-industry rows that pdfplumber
    sometimes appends from the next sub-section (e.g. 港股通投资组合 header).

    Keep rows whose first cell is a single uppercase letter (A-S industry code)
    or the 合计 (total) row. The 合计 row often has an empty first cell with
    "合计" in the second column, so we check the whole row for 合计.
    """
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        code = r[0].strip() if r else ""
        joined = "".join(r)
        if re.match(r"^[A-Z]$", code) or "合计" in joined:
            kept.append(r)
    return kept


def _filter_top10_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the top-10 table, keep only numbered data rows (序号 1-10)."""
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        seq = r[0].strip() if r else ""
        if seq.isdigit() and 1 <= int(seq) <= 10:
            kept.append(r)
    return kept


def _filter_asset_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the asset table, keep numbered rows + the 合计 total row."""
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        seq = r[0].strip() if r else ""
        if seq.isdigit() or seq == "合计":
            kept.append(r)
    return kept


def _filter_bond_type_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the bond-type table, keep numbered rows + 其中 sub-rows + 合计.

    Drops any trailing rows appended from the next sub-section.
    """
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        seq = r[0].strip() if r else ""
        second = r[1].strip() if len(r) > 1 else ""
        if seq.isdigit() or "合计" in second or second.startswith("其中"):
            kept.append(r)
    return kept


def _filter_remaining_maturity_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the remaining-maturity table, keep numbered buckets, 其中 sub-rows,
    and the 合计 total row."""
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        seq = r[0].strip() if r else ""
        second = r[1].strip() if len(r) > 1 else ""
        if (seq.isdigit() or seq == "合计"
                or second.startswith("其中") or "天" in second):
            kept.append(r)
    return kept


def _filter_top10_bonds_rows(rows: List[List[str]]) -> List[List[str]]:
    """For the top-10 bond table, keep only numbered data rows (序号 1-10)
    whose second cell is a numeric bond code.

    The numeric-code check rejects trailing tables (e.g. 红利再投) that share
    the 6-column shape and a 1-10 sequence number but are not bond holdings.
    """
    if not rows:
        return rows
    header = rows[0]
    kept = [header]
    for r in rows[1:]:
        seq = r[0].strip() if r else ""
        code = r[1].strip() if len(r) > 1 else ""
        if (seq.isdigit() and 1 <= int(seq) <= 10
                and code.isdigit()):
            kept.append(r)
    return kept


_SECTION_FILTERS = {
    SECTION_TOP10: _filter_top10_rows,
    SECTION_INDUSTRY: _filter_industry_rows,
    SECTION_ASSET: _filter_asset_rows,
    SECTION_BOND_TYPE: _filter_bond_type_rows,
    SECTION_REMAINING_MATURE: _filter_remaining_maturity_rows,
    SECTION_TOP10_BONDS: _filter_top10_bonds_rows,
}


def _extract_fund_info(pdf: pdfplumber.PDF) -> Dict[str, str]:
    """Extract basic fund metadata from the 基金产品概况 section.

    Chinese fund quarterly reports include a 2-column key-value table on the
    first 2-4 pages (基金简称, 基金主代码, 业绩比较基准, …). We scan those
    pages with ``extract_tables`` and collect recognised keys. The report
    period (e.g. "2026年第2季度") is read from the page-1 heading text.

    Returns a dict of {key: value}; only keys actually found are included.
    """
    info: Dict[str, str] = {}
    target_keys = (
        "基金简称", "场内简称", "基金主代码", "基金运作方式",
        "基金合同生效日", "报告期末基金份额总额",
        "业绩比较基准", "风险收益特征",
        "基金管理人", "基金托管人",
    )
    # 基金产品概况 usually spans pages 2-3; scan the first 4 pages to be safe.
    for page in pdf.pages[:4]:
        tables = page.extract_tables() or []
        for tbl in tables:
            for row in tbl:
                if not row or len(row) < 2:
                    continue
                key = _clean_cell(row[0])
                val = _clean_cell(row[1])
                if not key:
                    continue
                for target in target_keys:
                    if key == target and target not in info and val:
                        info[target] = val
                        break
    # Report period — from the page-1 heading (e.g. "2026 年第 2 季度报告").
    if pdf.pages:
        page1 = pdf.pages[0].extract_text() or ""
        m = re.search(r"(\d{4})\s*年\s*第\s*([一二三四1-4])\s*季度报告", page1)
        if m:
            info["报告期"] = f"{m.group(1)}年第{m.group(2)}季度"
    return info


def _write_identify_csv(
    out_dir: Path,
    prefix: str,
    fund_info: Dict[str, str],
    section_row_counts: Dict[str, int],
) -> Path:
    """Write the key-value identify CSV.

    Rows:
      - Basic fund metadata (基金简称, 业绩比较基准, …) from *fund_info*.
      - One row per 投资组合报告 sub-section: ``投资组合报告-<label>`` →
        ``有内容(N行)`` or ``无内容``.

    The identify CSV helps tell individual extracted CSVs apart: it records
    which sections had content and which were 0-byte placeholders.
    """
    fname = f"{prefix}{IDENTIFY_CSV_NAME}" if prefix else IDENTIFY_CSV_NAME
    csv_path = out_dir / fname
    rows: List[List[str]] = [["key", "value"]]
    # Metadata keys in a stable, readable order.
    meta_order = (
        "基金简称", "场内简称", "基金主代码", "报告期",
        "基金运作方式", "基金合同生效日", "报告期末基金份额总额",
        "业绩比较基准", "风险收益特征",
        "基金管理人", "基金托管人",
    )
    for key in meta_order:
        if key in fund_info:
            rows.append([key, fund_info[key]])
    # Any extra metadata keys not in meta_order.
    for key, val in fund_info.items():
        if key not in meta_order:
            rows.append([key, val])
    # Per-section content summary under 投资组合报告.
    for section in SECTIONS:
        label = SECTION_LABELS_CN[section]
        n = section_row_counts.get(section, 0)
        status = f"有内容({n}行)" if n > 0 else "无内容"
        rows.append([f"投资组合报告-{label}", status])
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return csv_path


def extract_sections(pdf_path: Path) -> Dict[str, List[List[str]]]:
    """Extract the target sections from *pdf_path*.

    Returns {section_label: rows} where rows[0] is the header and the rest
    are data rows. Only sections with content (header + ≥1 data row) are
    included; missing sections are omitted.
    """
    out: Dict[str, List[List[str]]] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        raw = _extract_section_rows(pdf)
    for section, rows in raw.items():
        flt = _SECTION_FILTERS.get(section)
        if flt:
            rows = flt(rows)
        if len(rows) >= 2:  # header + at least 1 data row
            out[section] = rows
    return out


def write_section_csvs(
    pdf_path: Path,
    out_dir: Optional[Path] = None,
    *,
    prefix: str = "",
) -> Dict[str, Path]:
    """Extract sections from *pdf_path* and write one CSV per section.

    All six target sections are always written next to the PDF (same
    directory unless *out_dir* is given). Sections found in the PDF produce
    a normal CSV (header + data rows); sections not found produce a 0-byte
    placeholder CSV so every PDF has a consistent set of outputs.

    A key-value identify CSV (``{prefix}identify.csv``) is also written,
    recording basic fund metadata and which 投资组合报告 sub-sections had
    content. This is the "extraction done" marker consulted by
    :func:`downloads.etf.szse.archive.reports.all_csvs_exist`.

    Filenames: ``{prefix}{csv_name}`` — e.g. ``160812_2026Q2_top10_holdings.csv``.

    Returns {section_label: csv_path} for ALL sections (including 0-byte
    placeholders).
    """
    if out_dir is None:
        out_dir = pdf_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(str(pdf_path)) as pdf:
        raw = _extract_section_rows(pdf)
        fund_info = _extract_fund_info(pdf)

    written: Dict[str, Path] = {}
    section_row_counts: Dict[str, int] = {}
    for section in SECTIONS:
        rows = raw.get(section)
        flt = _SECTION_FILTERS.get(section)
        if flt and rows:
            rows = flt(rows)
        fname = f"{prefix}{CSV_NAMES[section]}" if prefix else CSV_NAMES[section]
        csv_path = out_dir / fname
        if rows and len(rows) >= 2:  # header + >= 1 data row
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                for row in rows:
                    writer.writerow(row)
            section_row_counts[section] = len(rows) - 1  # exclude header
        else:
            # Section not found in PDF — write 0-byte placeholder.
            csv_path.write_bytes(b"")
            section_row_counts[section] = 0
        written[section] = csv_path

    _write_identify_csv(out_dir, prefix, fund_info, section_row_counts)
    return written
