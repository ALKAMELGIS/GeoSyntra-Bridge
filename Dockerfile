# GeoSyntra Bridge — server image (Linux). Users only need a browser.
#   docker build -t geosyntra-bridge .
#   docker compose -f deploy/docker-compose.yml up -d
FROM python:3.12-slim

# DWG is read by the built-in reader (geosyntra_bridge/cad/dwg): no outside converter is installed.

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# the web interface is already built into geosyntra_bridge/api/static
COPY geosyntra_bridge geosyntra_bridge
COPY schemas schemas
COPY knowledge knowledge
COPY mapping_rules mapping_rules

ENV GSB_HOST=0.0.0.0 \
    GSB_PORT=8600 \
    GSB_DATA_DIR=/data \
    GSB_WORKERS=4 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8600
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8600/api/health',timeout=4).status==200 else 1)"
RUN useradd -m -u 1000 gsb && mkdir -p /data && chown gsb /data
USER gsb
CMD ["python", "-m", "geosyntra_bridge.server"]
