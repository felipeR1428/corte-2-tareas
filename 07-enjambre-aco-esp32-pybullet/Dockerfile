FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HTTP_BIND=0.0.0.0 DATA_DIR=/app/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY firmware ./firmware
COPY simulador ./simulador
EXPOSE 8080/tcp 4211/udp
CMD ["python", "-m", "simulador.app"]
