FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION=true

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY api_client.py streamlit_app.py ./

# Sin privilegios de root dentro del contenedor.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8501
# ECS Express Mode comprueba la ruta indicada en health_check_path (/_stcore/health).
CMD ["streamlit", "run", "streamlit_app.py"]
