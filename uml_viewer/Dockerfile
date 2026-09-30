FROM python:3.11-alpine

WORKDIR /viewer
COPY server.py /viewer/server.py
EXPOSE 8080
USER 65532:65532
CMD ["python", "-u", "/viewer/server.py"]
