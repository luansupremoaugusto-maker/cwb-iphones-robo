FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md requirements.lock.txt ./
COPY app ./app
COPY main.py worker.py ./
COPY data/faq.yaml /app/config/faq.yaml

RUN python -m pip install --upgrade pip \
    && python -m pip install --require-hashes -r requirements.lock.txt

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
