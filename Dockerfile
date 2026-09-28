FROM python:3.13.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system proxyapi \
    && useradd --system --gid proxyapi --create-home proxyapi

WORKDIR /app

COPY requirements/runtime.txt requirements/runtime.txt
RUN python -m pip install --no-cache-dir --requirement requirements/runtime.txt

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN python -m pip install --no-cache-dir --no-deps .

USER proxyapi

EXPOSE 8000

CMD ["uvicorn", "proxy_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
