#!/usr/bin/env bash

python -m downloads.stock.sse.dividend
python -m downloads.stock.szse.dividend
python -m downloads.etf.szse.archive reports
python -m downloads.etf.huatai_pb.dividends