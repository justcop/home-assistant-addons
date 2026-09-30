FROM python:3.12-alpine
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN apk add --no-cache tzdata && pip install --no-cache-dir -r requirements.txt
COPY engine.py app.py collectors.py ./
COPY templates ./templates
CMD ["python", "app.py"]
