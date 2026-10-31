# ==============================================================================
# Dockerfile para Despliegue 24/7 en Hugging Face Spaces (16 GB RAM Gratis) / Render
# ==============================================================================
FROM python:3.11-slim

# Variables de entorno para ejecución desatendida y gráficos headless
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MPLBACKEND=Agg \
    PORT=7860

# Instalar fuentes del sistema (DejaVu) y utilidades necesarias para renderizado PNG y red
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root con UID 1000 (requisito obligatorio de Hugging Face Spaces)
RUN useradd -m -u 1000 user

USER user
ENV HOME=/home/user \
    PATH="/home/user/.local/bin:${PATH}"

WORKDIR $HOME/app

# Instalar dependencias de Python (CPU-only para PyTorch para imagen ligera y rápida)
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copiar código fuente del agente y asegurar carpeta de informes con permisos de escritura
COPY --chown=user . .
RUN mkdir -p $HOME/app/informes/auditorias \
             $HOME/app/informes/h2h \
             $HOME/app/informes/mercados \
             $HOME/app/informes/evaluacion

# Exponer el puerto estándar de Hugging Face Spaces / Render
EXPOSE 7860

# Iniciar el demonio inmortal del Bot Interactivo + Servidor HTTP Keep-Alive
CMD ["python", "-u", "bot_interactivo.py"]
