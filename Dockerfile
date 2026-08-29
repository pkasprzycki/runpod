FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV BLENDER_VERSION=4.2.9
ENV BLENDER_BIN=/usr/local/bin/blender

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    xz-utils \
    python3 \
    python3-pip \
    libxrender1 \
    libxi6 \
    libxkbcommon0 \
    libsm6 \
    libxxf86vm1 \
    libgl1 \
    libxfixes3 \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

RUN wget -q "https://download.blender.org/release/Blender4.2/blender-${BLENDER_VERSION}-linux-x64.tar.xz" \
  && tar -xJf "blender-${BLENDER_VERSION}-linux-x64.tar.xz" -C /opt \
  && ln -s "/opt/blender-${BLENDER_VERSION}-linux-x64/blender" "${BLENDER_BIN}" \
  && rm "blender-${BLENDER_VERSION}-linux-x64.tar.xz"

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY handler.py render.py ./

CMD ["python3", "-u", "handler.py"]
