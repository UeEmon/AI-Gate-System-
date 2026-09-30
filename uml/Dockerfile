FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless graphviz fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# Use the portable Java release, not a native executable requiring AVX2.
RUN python -c "import hashlib,pathlib,urllib.request; data=urllib.request.urlopen('https://github.com/plantuml/plantuml/releases/download/v1.2026.8/plantuml-1.2026.8.jar', timeout=120).read(); assert hashlib.sha256(data).hexdigest() == '5e1ecfa8ecd32c90b03bbf3b1eb6f020943f98ab0fcf4032be31a0002ee2c462'; pathlib.Path('/opt/plantuml.jar').write_bytes(data)"
RUN pip install --no-cache-dir pylint==4.0.9
ENV PLANTUML_JAR=/opt/plantuml.jar PYLINTHOME=/tmp/pylint HOME=/tmp
WORKDIR /uml
COPY server.py /uml/server.py
COPY generator.py /uml/generator.py
EXPOSE 8080
USER 65532:65532
CMD ["python", "-u", "/uml/server.py"]
