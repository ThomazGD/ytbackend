FROM python:3.11-slim

# Instala FFmpeg e dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Configuração do ambiente
WORKDIR /app
ENV PORT=8080
ENV FILES_DIR=/app/files

# Copia e instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Cria diretório para arquivos
RUN mkdir -p ${FILES_DIR}

# Expõe a porta
EXPOSE 8080

# Comando de inicialização
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
