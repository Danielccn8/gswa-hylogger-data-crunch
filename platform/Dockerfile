# One image for the Explorer API and background worker.
FROM python:3.14-slim
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY platform/requirements.lock /tmp/requirements.lock
RUN pip install -r /tmp/requirements.lock
COPY pyproject.toml ./
COPY configs ./configs
COPY src ./src
COPY platform ./platform
EXPOSE 8000
CMD ["uvicorn", "--app-dir", "platform", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
