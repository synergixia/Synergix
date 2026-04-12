FROM python:3.10-slim

# Instalar Node.js y dependencias del sistema (para Greenfield)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# FORZAR QUE PYTHON VEA LA CARPETA RAÍZ
ENV PYTHONPATH=/app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el proyecto Synergix al contenedor
COPY . .

# Ejecutar como módulo para evitar errores de importación
CMD ["python", "-m", "aisynergix.bot.bot"]
