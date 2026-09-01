"""builds package init.

Eagerly imports ``_algo_kernels`` so every builds script start compiles
its CUDA kernels once up-front (NVRTC, ~0.2 s; see _algo_kernels
docstring for the algo -> kernel convention). Failure is non-fatal:
scripts continue on CPU-only paths.
"""
from builds import _algo_kernels  # noqa: F401  (import = eager compile)
