# ==== build/run base ====
FROM python:3.11-slim

# Evita buffering no log
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Pasta de trabalho
WORKDIR /app

# System deps (FFmpeg, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala deps Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia código
COPY . .

# Exponha a porta do Uvicorn
EXPOSE 8000

# Variável para apontar pasta de arquivos (ajuda a persistir via volume)
ENV FILES_DIR=/app/files

# Cria diretório de arquivos
RUN mkdir -p ${FILES_DIR}

# Comando de inicialização
# (Use workers=1: yt-dlp/FFmpeg consomem CPU; escale réplicas se precisar)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
