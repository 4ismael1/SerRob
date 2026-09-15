FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY serverbot ./serverbot
RUN useradd --create-home bot && mkdir -p /app/data && chown -R bot:bot /app
USER bot
CMD ["python", "main.py"]
