FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    EASYOCR_MODULE_PATH=/models/easyocr YOLO_CONFIG_DIR=/data/ultralytics
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir 'torch>=2.2,<3' 'torchvision>=0.17,<1' --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 gate && mkdir /data /models && chown gate:gate /data /models
COPY --chown=gate:gate app.py web.py events.py evidence.py registry_csv.py mail_delivery.py ./
COPY --chown=gate:gate templates/ templates/
COPY --chown=gate:gate static/ static/
USER gate
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["python","web.py","--host","0.0.0.0","--data","/data","--model","/models/yolo11n.pt"]
