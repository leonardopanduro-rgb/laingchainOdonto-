FROM python:3.14-slim
WORKDIR /service
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY recepcion ./recepcion
RUN pip install --no-cache-dir --no-deps . && useradd --create-home reception && mkdir /data && chown reception /data
USER reception
ENV RECEPTION_DB=/data/reception.db
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "recepcion.api:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1", "--no-access-log"]
