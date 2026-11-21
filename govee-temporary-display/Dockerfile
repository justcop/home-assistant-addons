ARG BUILD_FROM
FROM ${BUILD_FROM}
RUN apk add --no-cache openssh-client
COPY reactive.py /reactive.py
COPY run.py /run.py
COPY index.html /index.html
CMD ["python3", "-u", "/run.py"]
