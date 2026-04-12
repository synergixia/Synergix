FROM ubuntu:22.04
RUN apt-get update && apt-get install -y build-essential git cmake
RUN git clone https://github.com/ggerganov/llama.cpp.git /app
WORKDIR /app
RUN cmake . -B build && cmake --build build --config Release -j$(nproc) --target llama-server
ENTRYPOINT ["./build/bin/llama-server"]
