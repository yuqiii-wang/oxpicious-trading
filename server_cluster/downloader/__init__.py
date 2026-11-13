"""server_cluster.downloader — the resident API entry over the
``downloads.*`` area (batch download jobs + the five resident stream
pollers this server supervises; see the cluster package docstring)."""

SERVICE = "server_cluster.downloader"
AREA = "downloader"
