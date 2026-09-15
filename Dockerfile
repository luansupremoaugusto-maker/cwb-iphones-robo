FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y fonts-noto-core fonts-noto-cjk \
    && test -s /usr/share/fonts/truetype/noto/NotoSans-Regular.ttf \
    && test -s /usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf \
    && test -s /usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf \
    && test -s /usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md requirements.lock.txt ./
COPY app ./app
COPY main.py worker.py ./
COPY data/faq.yaml /app/config/faq.yaml

RUN python -m pip install --upgrade pip \
    && python -m pip install --require-hashes -r requirements.lock.txt \
    && python -c "from app.admin import _CATALOG_PDF_FONTS; assert all(_CATALOG_PDF_FONTS[key].startswith('CatalogNoto') for key in ('default', 'arabic', 'devanagari', 'symbols'))"

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
