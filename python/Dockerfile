FROM python:3.11-slim

# Install SSH server + basics
RUN apt-get update && apt-get install -y \
    openssh-server \
    bash \
    && mkdir /var/run/sshd

# Create working dir
WORKDIR /workspace

# Copy startup script
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
