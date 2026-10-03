FROM python:3.11-slim
WORKDIR /app
COPY platform ./platform
COPY configs/delivery_cohort.json ./configs/delivery_cohort.json
RUN pip install --no-cache-dir 'fastapi>=0.115,<1' 'psycopg[binary]>=3.2,<4' 'uvicorn>=0.30,<1'
EXPOSE 8000
CMD ["uvicorn", "--app-dir", "platform", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
