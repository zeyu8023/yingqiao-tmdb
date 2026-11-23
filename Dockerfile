ARG PYTHON_IMAGE=python:3.12-alpine
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8080
WORKDIR /app
RUN addgroup -g 10001 relay && adduser -D -u 10001 -G relay relay \
    && mkdir /data && chown relay:relay /data
COPY --chown=relay:relay app.py /app/
COPY --chown=relay:relay web /app/web
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; host=os.getenv('HOST', '0.0.0.0'); urllib.request.urlopen('http://'+('127.0.0.1' if host=='0.0.0.0' else host)+':'+os.environ['PORT']+'/health', timeout=3)"
CMD ["python", "app.py"]
