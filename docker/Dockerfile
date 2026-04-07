# Dockerfile Profesional para Synergix (ARM64)
FROM python:3.10-slim

# Evitar avisos de debconf
ENV DEBIAN_FRONTEND=noninteractive

# Instalar dependencias del sistema fundamentales
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    curl \
    zstd \
    gnupg \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Instalar Node.js 20 (Necesario para el SDK de Greenfield)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código del proyecto
COPY . .

# Instalar dependencias de Node.js (Greenfield SDK)
RUN npm install

# Comando por defecto
CMD ["python3", "aisynergix/bot/bot.py"]
