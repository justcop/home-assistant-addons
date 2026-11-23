FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0
RUN python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY app /app
COPY .delivery/contract.py /app/contract.py
CMD ["/command/with-contenv", "/opt/venv/bin/python", "server.py"]
