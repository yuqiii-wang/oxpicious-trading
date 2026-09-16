from .__main__ import BaselineBuild

__all__ = ["BaselineBuild"]


async def main():
    """Backward-compatible shim: parse sys.argv, run BaselineBuild."""
    build = BaselineBuild()
    build.parse_argv()
    await build.amain()
