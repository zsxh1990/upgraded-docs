FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd -m -r appuser && \
    mkdir -p /app/data /app/wiki && \
    chown -R appuser:appuser /app
COPY --chown=appuser:appuser . .
USER appuser
EXPOSE 8080
ENV KB_HOST=0.0.0.0
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1
CMD ["python", "backend/server.py"]
