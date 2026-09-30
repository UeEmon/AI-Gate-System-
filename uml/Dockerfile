FROM python:3.11-alpine

RUN pip install --no-cache-dir pylint==4.0.9
WORKDIR /uml
COPY server.py /uml/server.py
COPY generator.py /uml/generator.py
EXPOSE 8080
USER 65532:65532
CMD ["python", "-u", "/uml/server.py"]
