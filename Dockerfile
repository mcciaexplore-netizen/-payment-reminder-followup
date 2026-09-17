FROM python:3.12-slim
RUN useradd --create-home --uid 1000 appuser
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=appuser:appuser . .
USER appuser
ENV APP_MODE=demo PYTHONUNBUFFERED=1
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
