# Dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV CHROME_BIN=/usr/bin/chromium
ENV PATH="/opt/venv/bin:$PATH"

# Instalar dependencias del sistema + chromium + chromedriver
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates gnupg2 unzip \
    chromium chromium-driver \
    build-essential libglib2.0-0 libnss3 libx11-6 libxcomposite1 libxcursor1 libxdamage1 libxi6 libxtst6 libasound2 libatk1.0-0 libpangocairo-1.0-0 libatk-bridge2.0-0 libcups2 libxrandr2 libxrender1 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# Crear virtualenv e instalar requirements
RUN python -m venv /opt/venv
COPY requirements.txt /app/requirements.txt
WORKDIR /app
RUN /opt/venv/bin/pip install --upgrade pip
RUN /opt/venv/bin/pip install -r /app/requirements.txt

# Copiar el código y el script de arranque
COPY . /app
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

# Exponer (informativo)
EXPOSE 10000

# Start script maneja la expansión de $PORT y muestra diagnósticos si falla
CMD ["/app/start.sh"]
