ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

COPY rootfs/app/requirements.txt /app/requirements.txt
RUN apk add --no-cache build-base libffi-dev openssl-dev \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && apk del build-base libffi-dev openssl-dev

COPY rootfs/app /app

ENV BACKEND_HOST=0.0.0.0
ENV BACKEND_PORT=8099
ENV WEBUNTIS_SETTINGS_DIR=/data

CMD ["python", "/app/main.py"]
