FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDIT_DB=/data/audits.db \
    DERRIERE_PROXY=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# La base SQLite vit dans /data : à monter comme volume persistant,
# sinon les rapports et les leads sont perdus à chaque redéploiement.
RUN useradd --create-home audit && mkdir -p /data && chown audit /data
USER audit
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/sante')" || exit 1

# --proxy-headers : l'app tourne derrière le proxy de Coolify, qui transmet
# l'adresse et le protocole (https) d'origine.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
