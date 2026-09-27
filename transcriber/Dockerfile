FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Torch CPU отдельно — самый тяжелый слой, кэшируется.
# torchaudio >= 2.9 требует torchcodec для аудио-I/O, а GigaAM зовет torchaudio.load —
# поэтому пришпилены совместимые 2.8.x
RUN pip install --no-cache-dir "torch==2.8.*" "torchaudio==2.8.*" --index-url https://download.pytorch.org/whl/cpu \
    || pip install --no-cache-dir "torch==2.8.*" "torchaudio==2.8.*"

RUN pip install --no-cache-dir \
    "gigaam[torch] @ git+https://github.com/salute-developers/GigaAM.git" \
    fastapi "uvicorn[standard]" python-multipart silero-vad soundfile numpy

# Прогрев весов модели в слой образа (первый запуск без скачивания).
# Если сеть недоступна при сборке — не валим билд, скачается при старте.
ARG GIGAAM_MODEL=v3_e2e_rnnt
RUN python -c "import gigaam; gigaam.load_model('${GIGAAM_MODEL}')" || true

COPY app.py .

ENV GIGAAM_MODEL=v3_e2e_rnnt
ENV OMP_NUM_THREADS=4

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000"]
