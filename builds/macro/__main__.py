from _common.data_build import DataBuild


class MacroBuild(DataBuild):
    """``python -m builds.macro`` — delegates to builds.macro.pipeline."""

    allow_unknown_args = True  # pipeline parses sys.argv itself

    async def run(self) -> None:
        from builds.macro.pipeline import MacroPipeline

        await MacroPipeline().run()


if __name__ == "__main__":
    MacroBuild().execute()
