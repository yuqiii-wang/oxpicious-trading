"""llm_agents.llm_ask.cli — argparse CLI.

Usage::

    python -m llm_agents.llm_ask ask --question "…" [--provider zhipu] \
        [--model glm-5.2] [--lang zh] [--system "…"] [--temperature 0.3] \
        [--json]
    python -m llm_agents.llm_ask ask --payload-file <path> \
        [--provider zhipu] [--model glm-4.5v] [--json]

``--question`` (plain ask) and ``--payload-file`` (the data_viz AI Ask
chart-adviser payload — see ``llm_agents.llm_ask.payload``; a payload
``onlineSearch: true`` routes through the online-search agent) are
mutually exclusive; exactly one is required. ``python -m llm_agents ask
…`` dispatches here too (see llm_agents.__main__). ``--json`` prints one
``@@LLM_ASK_JSON@@`` marker line with the machine-readable envelope
after the human logs — the same convention as the online-search CLI (its
logger also writes stdout); the Express ai-ask service scans for the
LAST marker line.
"""
from __future__ import annotations

# resource pre-check -- exit early when sys/GPU memory is insufficient.
# This agent is network-only (one JSON POST, no pandas/cudf), so it needs
# neither the 24 GiB VRAM nor the 36 GiB RAM the data pipelines demand --
# and every data_viz AI Ask spawns it, so the default gate (36 GiB) would
# block all UI asks on hosts whose WSL VM is smaller than that.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

from _common.build_commons import setup_utf8_stdout  # noqa: E402

setup_utf8_stdout()

import argparse  # noqa: E402
import json  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

from llm_agents.llm_ask.core.models import AskOptions  # noqa: E402
from llm_agents.llm_ask.payload import (  # noqa: E402
    ask_online_search, build_ask_request, read_payload_file,
)
from llm_agents.llm_ask.storage import persist_ask  # noqa: E402
from llm_agents.llm_ask.providers.registry import (  # noqa: E402
    PROVIDERS, get_provider,
)

logger = setup_logging("llm_ask")

# stdout marker prefixing the JSON result envelope (the online-search CLI
# convention: the logger stream also writes stdout, so API callers scan for
# the LAST marker line).
RESULT_MARKER = "@@LLM_ASK_JSON@@"


async def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.llm_ask",
        description="Plain LLM ask agent (providers: "
                    f"{sorted(PROVIDERS)}): one question -> one chat "
                    "completion — no online search involved.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ask = sub.add_parser(
        "ask", help="ask one question — plain LLM completion, no search")
    src = p_ask.add_mutually_exclusive_group(required=True)
    src.add_argument("--question",
                     help="The question to send to the LLM.")
    src.add_argument(
        "--payload-file",
        help="data_viz AI Ask payload (question + plotInfo + screenshots) "
             "written by the Express ai-ask service — chart-adviser mode.")
    p_ask.add_argument("--provider", default="zhipu",
                       choices=sorted(PROVIDERS))
    p_ask.add_argument("--model", default=None,
                       help="Chat model (provider default; payloads with "
                            "screenshots default to the provider's vision "
                            "model — see core/vision.py).")
    p_ask.add_argument("--lang", default="zh",
                       help="Answer language, default zh (Chinese); "
                            "e.g. --lang en for English. Ignored in "
                            "--payload-file mode (the adviser mirrors the "
                            "question's language).")
    p_ask.add_argument("--system", default=None,
                       help="Override the default system prompt.")
    p_ask.add_argument("--temperature", type=float, default=None,
                       help="Sampling temperature within [0, 1] "
                            "(provider default).")
    p_ask.add_argument("--json", action="store_true",
                       help="Print a machine-readable @@LLM_ASK_JSON@@ line.")

    args = ap.parse_args()
    provider = get_provider(args.provider)()

    if args.payload_file:
        payload = read_payload_file(args.payload_file)
        # Payload-mode asks are persisted to the ask-history tables
        # (text.llm_qa_by_ask + context/images/keywords — see
        # llm_agents.llm_ask.storage) after the answer exists; failures
        # persist too (status='failed'), then re-raise so the service
        # still reports them. Persistence itself is fail-soft.
        try:
            if payload.online_search:
                logger.info("ask (payload online-search chart=%s screenshots=0 "
                            "theme=%s)",
                            (payload.plot_info.get("chart") or {}).get("title"),
                            payload.theme_mode)
                result = await ask_online_search(
                    payload, provider, model=args.model)
            else:
                question, opts, context = build_ask_request(
                    payload, provider.name, model=args.model)
                logger.info("ask (payload chart=%s screenshots=%d theme=%s)",
                            (payload.plot_info.get("chart") or {}).get("title"),
                            len(payload.screenshots), payload.theme_mode)
                result = await provider.ask(question, opts, context=context)
        except Exception as exc:
            await persist_ask(
                payload, error_tail=f"{type(exc).__name__}: {exc}")
            raise
        await persist_ask(payload, result)
    else:
        opts = AskOptions(model=args.model, lang=args.lang, system=args.system,
                          temperature=args.temperature)
        result = await provider.ask(args.question, opts)
    logger.info("ANSWER (%s/%s):\n%s", result.provider, result.model,
                result.answer)
    if args.json:
        print(RESULT_MARKER + json.dumps(result.to_dict(),
                                         ensure_ascii=False))
