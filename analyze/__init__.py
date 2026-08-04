"""analyze — analysis pipeline package.

Each subpackage is a self-contained analysis that populates a table under
the ``analysis.`` schema. All subpackages follow the same layout:

    analyze/<name>/
        __init__.py
        config.py      — constants (TABLE, ANALYSIS_NAME, DESCRIPTION, ...)
        helpers.py     — pure helper functions (optional)
        fetch.py       — async DB fetch primitives (optional)
        compute.py     — pandas/numpy transformation logic (optional)
        __main__.py    — async main() entry point (run via ``python -m``)

Run order (dependency-aware) is encoded in ``main.sh`` at project root.
"""
