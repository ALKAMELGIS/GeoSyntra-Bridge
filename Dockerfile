# GeoSyntra Bridge — server image (Linux). Users only need a browser.
#   docker build -t geosyntra-bridge .
#   docker compose -f deploy/docker-compose.yml up -d
FROM python:3.12-slim

# DWG reading: LibreDWG's dwg2dxf from Debian when available; the free ODA File Converter can be added with
#   --build-arg ODA_DEB_URL=<link to the ODAFileConverter .deb from opendesign.com>  (accepting the ODA licence)
ARG ODA_DEB_URL=""
RUN apt-get update \
 && (apt-get install -y --no-install-recommends libredwg-tools || true) \
 && if [ -n "$ODA_DEB_URL" ]; then apt-get install -y --no-install-recommends curl xvfb \
      && curl -fsSL "$ODA_DEB_URL" -o /tmp/oda.deb && apt-get install -y /tmp/oda.deb && rm /tmp/oda.deb; fi \
 && rm -rf /var/lib/apt/lists/*

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
