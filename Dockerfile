FROM python:3.12-slim

WORKDIR /app

# Build deps for paramiko/cffi; removed after install to keep the image slim.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY setup.cfg setup.py VERSION ./
COPY circadiand ./circadiand

RUN uv pip install --system --no-cache . \
    && apt-get purge -y build-essential libffi-dev \
    && apt-get autoremove -y

EXPOSE 8000

CMD ["python", "-m", "circadiand"]
