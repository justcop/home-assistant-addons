ARG BUILD_FROM
FROM ${BUILD_FROM}
RUN apk add --no-cache openssh-client && python3 -m pip install --no-cache-dir Pillow==11.3.0
COPY artwork/ /artwork/
COPY artwork_loader.py /artwork_loader.py
COPY artwork_modes.py /artwork_modes.py
COPY ha_countdown.py /ha_countdown.py
COPY renderers.py /renderers.py
COPY reactive.py /reactive.py
COPY run.py /run.py
COPY index.html /index.html
CMD ["/command/with-contenv", "python3", "-u", "/run.py"]
