ARG BUILD_FROM
FROM ${BUILD_FROM}
RUN apk add --no-cache openssh-client
COPY gta_vi_fullscreen.py /gta_vi_fullscreen.py
COPY gta_vi_art.py /gta_vi_art.py
COPY ha_countdown.py /ha_countdown.py
COPY renderers.py /renderers.py
COPY reactive.py /reactive.py
COPY run.py /run.py
COPY index.html /index.html
CMD ["/command/with-contenv", "python3", "-u", "/run.py"]
