FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

# No native-build deps needed here (no psycopg2 — this service uses its own
# SQLite file, deliberately not sharing the retail-credit-api Postgres
# database; see mock-payment-gateway/README.md).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8100

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
