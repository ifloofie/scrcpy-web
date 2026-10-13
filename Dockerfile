# scrcpy-web: control an Android device plugged into your server from a
# browser (noVNC) or any VNC client.
#
# Versions are pinned here. scripts/bump-scrcpy.sh (run weekly by
# .github/workflows/update-scrcpy.yml) updates SCRCPY_VERSION.
ARG UBUNTU_VERSION=26.04
ARG SCRCPY_VERSION=5.0.1

# ---------------------------------------------------------------------------
# Stage 1: build the scrcpy client at a pinned release tag
# ---------------------------------------------------------------------------
FROM ubuntu:${UBUNTU_VERSION} AS scrcpy-build

ARG SCRCPY_VERSION
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git wget gcc libc6-dev pkg-config meson ninja-build \
      libsdl3-dev libavcodec-dev libavformat-dev libavutil-dev \
      libswresample-dev libusb-1.0-0-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "v${SCRCPY_VERSION}" \
      https://github.com/Genymobile/scrcpy.git .

# The device-side server is a prebuilt jar published with each release. Its
# URL and SHA-256 are taken from upstream's own install_release.sh at the same
# tag, so bumping SCRCPY_VERSION is the only change an update needs.
RUN set -eu; \
    url="$(sed -n 's/^PREBUILT_SERVER_URL=//p' install_release.sh)"; \
    sha="$(sed -n 's/^PREBUILT_SERVER_SHA256=//p' install_release.sh)"; \
    test -n "$url" && test -n "$sha"; \
    wget -q -O scrcpy-server "$url"; \
    echo "$sha  scrcpy-server" | sha256sum -c -; \
    meson setup build --buildtype=release --strip -Db_lto=true \
      -Dprebuilt_server=scrcpy-server -Dv4l2=false -Dvaapi=false; \
    ninja -C build; \
    DESTDIR=/out ninja -C build install; \
    # Work out which runtime packages the binary links against, so the final
    # stage installs exactly those (names change between Ubuntu releases).
    ldd /out/usr/local/bin/scrcpy | awk '/=> \//{print $3}' \
      | xargs -r realpath | xargs -r dpkg -S 2>/dev/null | cut -d: -f1 \
      | sort -u > /out/runtime-packages.txt; \
    cat /out/runtime-packages.txt; \
    test -s /out/runtime-packages.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM ubuntu:${UBUNTU_VERSION} AS runtime

ARG DEBIAN_FRONTEND=noninteractive
ARG VERSION=dev
ARG SCRCPY_VERSION

LABEL org.opencontainers.image.title="scrcpy-web" \
      org.opencontainers.image.description="Control a USB-connected Android device from a browser or VNC client using scrcpy" \
      org.opencontainers.image.source="https://github.com/ifloofie/scrcpy-web" \
      org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.version="${VERSION}" \
      io.github.scrcpy.version="${SCRCPY_VERSION}"

COPY --from=scrcpy-build /out/runtime-packages.txt /tmp/runtime-packages.txt

RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends \
      adb \
      tigervnc-standalone-server tigervnc-tools \
      x11-xserver-utils xdotool \
      novnc \
      python3 python3-aiohttp \
      supervisor tini \
      ca-certificates \
      $(cat /tmp/runtime-packages.txt) \
 && rm -rf /var/lib/apt/lists/* /tmp/runtime-packages.txt

COPY --from=scrcpy-build /out/usr/local/bin/scrcpy /usr/local/bin/scrcpy
COPY --from=scrcpy-build /out/usr/local/share/scrcpy /usr/local/share/scrcpy
COPY rootfs/ /

ENV HOME=/root \
    DISPLAY=:1 \
    SDL_VIDEO_DRIVER=x11 \
    SDL_AUDIODRIVER=dummy \
    SCRCPY_WEB_VERSION=${VERSION} \
    WEB_PORT=8080 \
    DATA_DIR=/data

VOLUME ["/data"]
EXPOSE 8080 5900 5037

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python3", "/opt/scrcpy-web/bin/healthcheck.py"]

ENTRYPOINT ["/usr/bin/tini", "--", "/opt/scrcpy-web/bin/init.sh"]
