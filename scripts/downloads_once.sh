#!/usr/bin/env bash

python -m downloads.stock.szse.archive
python -m downloads.etf.szse.archive
python -m downloads.index.szse.archive
python -m downloads.stock.sse.archive
python -m downloads.stock.bse.archive
python -m downloads.futures.cffex.archive
python -m downloads.etf.huatai_pb.archive