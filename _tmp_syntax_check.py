"""Temporary: syntax + import check for the chunked-build refactor."""
import ast
import importlib

files = [
    "analyze/_common/upsert.py",
    "analyze/_common/__init__.py",
    "analyze/mov_ave_spread/__main__.py",
    "analyze/mov_ave_spread/rsi.py",
]
for f in files:
    ast.parse(open(f, encoding="utf-8").read(), f)
print("syntax OK for", len(files), "files")

# Import check (catches NameError / missing symbols at module load).
import analyze._common  # noqa: F401
import analyze._common.upsert as u  # noqa: F401
assert hasattr(u, "build_and_insert_chunked")
assert hasattr(u, "group_df_by_date_chunks")
assert hasattr(u, "_filter_per_sec_type_chunk")
import analyze.mov_ave_spread.rsi as r  # noqa: F401
assert hasattr(r, "run_rsi")
print("imports OK; build_and_insert_chunked + group_df_by_date_chunks present")
