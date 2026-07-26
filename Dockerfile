FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY etl/ ./etl/
COPY sql/ ./sql/


RUN useradd --create-home etl && chown -R etl /app
USER etl

CMD ["python", "-m", "etl"]
