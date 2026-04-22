FROM python:3.11-slim

# Install SSH server, bash, dos2unix, AND jq
RUN apt-get update && apt-get install -y \
    openssh-server \
    bash \
    dos2unix \
    jq

# Create working dir
WORKDIR /workspace

# Copy startup script
COPY run.sh /run.sh

# Fix Windows line endings and make executable
RUN dos2unix /run.sh && chmod +x /run.sh

CMD ["/run.sh"]
