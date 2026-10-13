FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# зависимости — точные версии с хешами из lock-файла (pip-compile), отдельным слоем: пока
# lock не менялся, пересборка образа их не скачивает заново
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps .

COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 1000 bot
USER bot

EXPOSE 8080
CMD ["python", "-m", "pfbc_bot.main"]
