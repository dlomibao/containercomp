# containercomp: an Ubuntu 24.04 desktop in a container that Claude Code can
# drive with mouse, keyboard and screenshots.
#
#   podman build -t localhost/containercomp:native .
#   podman build --platform linux/amd64 -t localhost/containercomp:x86 .
#
# The amd64 build also installs Wine (disable with --build-arg WITH_WINE=0).
FROM docker.io/library/ubuntu:24.04

ARG WITH_WINE=1
ARG USERNAME=user

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DISPLAY=:1 \
    SCREEN_WIDTH=1280 \
    SCREEN_HEIGHT=800 \
    SCREEN_DEPTH=24

# Desktop, VNC/noVNC, input/screenshot tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo ca-certificates curl wget gnupg less nano vim-tiny file \
        unzip xz-utils bzip2 p7zip-full procps psmisc iproute2 \
        supervisor dbus dbus-x11 at-spi2-core \
        xvfb x11vnc novnc websockify x11-utils x11-xserver-utils \
        xfce4 xfce4-terminal mousepad ristretto \
        xdotool wmctrl scrot xclip \
        python3 python3-venv python3-pil \
        libfuse2t64 desktop-file-utils xdg-utils \
        fonts-dejavu fonts-liberation fonts-noto-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Wine for Windows apps (amd64 image only).
RUN if [ "$(dpkg --print-architecture)" = "amd64" ] && [ "$WITH_WINE" = "1" ]; then \
        dpkg --add-architecture i386 \
        && apt-get update \
        && apt-get install -y --no-install-recommends wine wine64 wine32:i386 winetricks cabextract \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Python env for the MCP server.
RUN python3 -m venv --system-site-packages /opt/ccu/venv \
    && /opt/ccu/venv/bin/pip install --no-cache-dir "mcp>=1.2,<2"

# Unprivileged desktop user with passwordless sudo. The base image ships an
# "ubuntu" user on uid 1000; replace it.
RUN userdel -r ubuntu 2>/dev/null || true \
    && useradd -m -u 1000 -s /bin/bash "$USERNAME" \
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$USERNAME \
    && chmod 0440 /etc/sudoers.d/90-$USERNAME \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

# Pre-create the Wine prefix: under x86 emulation the first wineboot takes
# minutes, and otherwise every new container would pay it on first launch.
RUN if command -v wine >/dev/null; then \
        su - "$USERNAME" -c 'WINEDEBUG=-all timeout 1200 xvfb-run -a sh -c "wineboot -i && wineserver -w"'; \
    fi

COPY rootfs/ /
RUN chmod +x /opt/ccu/bin/* /usr/local/bin/cu /usr/local/bin/ccu-install \
    && mkdir -p /home/$USERNAME/.config/xfce4/xfconf/xfce-perchannel-xml /home/$USERNAME/apps /home/$USERNAME/Desktop \
    && cp /etc/xdg/xfce4/panel/default.xml /home/$USERNAME/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml \
    && cp /opt/ccu/xfconf/*.xml /home/$USERNAME/.config/xfce4/xfconf/xfce-perchannel-xml/ \
    && chown -R $USERNAME:$USERNAME /home/$USERNAME

EXPOSE 6080
WORKDIR /home/$USERNAME
ENTRYPOINT ["/opt/ccu/bin/entrypoint"]
