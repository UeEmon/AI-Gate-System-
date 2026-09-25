FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    EASYOCR_MODULE_PATH=/models/easyocr YOLO_CONFIG_DIR=/data/ultralytics \
    PADDLE_HOME=/models/paddle PADDLE_PDX_MODEL_SOURCE=BOS GATE_OCR_BACKEND=auto
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# Separate install steps so Docker's build output identifies which package failed.
# Keep CPU-only wheels on both Intel and Apple Silicon Docker hosts.
RUN python -m pip install --no-cache-dir 'torch>=2.2,<3' 'torchvision>=0.17,<1' \
    --index-url https://download.pytorch.org/whl/cpu
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 gate && mkdir /data /models && chown gate:gate /data /models
COPY --chown=gate:gate app.py web.py events.py evidence.py registry_csv.py mail_delivery.py ocr_learning.py ocr_train.py ocr_backends.py accuracy_benchmark.py vision_train.py plate_geometry.py plate_rules.py ./
COPY --chown=gate:gate plate_pipeline.py plate_only_benchmark.py plate_benchmark_web.py inference_device.py ./
COPY --chown=gate:gate fast_plate_ocr_training.py fast_plate_ocr_train.py ./
COPY --chown=gate:gate scripts/ scripts/
COPY --chown=gate:gate training/ training/
COPY --chown=gate:gate aigate/ aigate/
COPY --chown=gate:gate templates/ templates/
COPY --chown=gate:gate static/ static/
USER gate
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"
CMD ["python","web.py","--host","0.0.0.0","--data","/data","--model","/models/yolo26s.pt"]
