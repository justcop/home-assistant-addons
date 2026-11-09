FROM python:3.15.2-alpine3.24
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN apk add --no-cache tzdata && pip install --no-cache-dir -r requirements.txt
COPY engine.py app.py collectors.py ./
COPY templates ./templates
CMD ["python", "app.py"]
