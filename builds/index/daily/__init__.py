from .__main__ import DailyBuild

__all__ = ["DailyBuild"]


async def main():
    """Backward-compatible shim: parse sys.argv, run DailyBuild."""
    build = DailyBuild()
    build.parse_argv()
    await build.amain()
