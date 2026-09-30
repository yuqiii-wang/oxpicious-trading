#!/usr/bin/env bash

python -m downloads.etf.sse.composition
python -m downloads.etf.szse.composition
python -m downloads.index.csindex.composition
python -m downloads.index.szse.composition
python -m downloads.etf.csindex.linked_etf
python -m downloads.macro.pboc.stats
# huatai-pb.com fund-site data (510880 红利ETF): monthly PCF composition
# snapshot + 基金概况 intro (NAV 成立以来 archive runs once; trend daily;
# dividends semi-annual)
python -m downloads.etf.huatai_pb.composition
python -m downloads.etf.huatai_pb.intro