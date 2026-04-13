# Etapa 1: Base de dependencias del sistema
FROM python:3.10-slim AS bot-python

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema requeridas para FAISS CPU en ARM64
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Actualizar pip e instalar numpy blindado (<2.0.0) antes que el resto
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Comando maestro: Primero el sync y luego el event-loop de Telegram
CMD ["sh", "-c", "python scripts/sync_brain.py && python -m aisynergix.bot.bot"]
