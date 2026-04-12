cat << 'EOF' > /root/Synergix/docker/Dockerfile
# ETAPA 1: EL MOTOR DE IA (C++)
FROM ubuntu:22.04 AS motor-ia
RUN apt-get update && apt-get install -y build-essential git cmake
RUN git clone https://github.com/ggerganov/llama.cpp.git /app
WORKDIR /app
RUN cmake . -B build && cmake --build build --config Release -j 4 --target llama-server
ENTRYPOINT ["./build/bin/llama-server"]

# ETAPA 2: EL BOT DE TELEGRAM (Python + Node)
FROM python:3.10-slim AS bot-python
RUN apt-get update && apt-get install -y curl gnupg
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash -
RUN apt-get install -y nodejs
RUN rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-m", "aisynergix.bot.bot"]
EOF
