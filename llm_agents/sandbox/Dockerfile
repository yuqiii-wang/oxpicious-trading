# llm_agents/sandbox/Dockerfile — the dockerized Python sandbox image.
#
# Base is the sagacient/cutepandas data-science image (py3.12 + pandas /
# numpy / scipy / scikit-learn / statsmodels / matplotlib / polars /
# pyarrow / duckdb / openpyxl / … — see the py-sandbox-env skill). The
# local `latest` tag's labels report version 1.0.4; `latest` is pinned
# here because that is the tag present on this host.
#
# The only additions: psycopg (sandbox scripts connect to the DB
# read-only via the compose-preset PG* env — verified to reach the
# `db` service as readonly_analytics) and the stdlib-only runner API.
FROM sagacient/cutepandas:latest

USER root
RUN pip install --no-cache-dir psycopg
COPY runner.py /opt/sandbox-runner/runner.py
USER pandas
WORKDIR /home/pandas
ENV SANDBOX_ROOT=/home/pandas/sandbox

EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8788/health', timeout=3)"

ENTRYPOINT ["python", "/opt/sandbox-runner/runner.py"]
CMD ["--host", "0.0.0.0", "--port", "8788"]
