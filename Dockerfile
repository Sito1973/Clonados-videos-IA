# Hosted deployment (EasyPanel or any Docker host). Mount a persistent volume at /app/runs.
# See docs/DEPLOY-EASYPANEL.md. Keys and APP_PASSWORD come from the host's environment, never the image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 APP_HOST=0.0.0.0 PORT=8770
RUN apt-get update && apt-get install -y --no-install-recommends rubberband-cli libgl1 libglib2.0-0 libegl1 libgles2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt setup-face-mesh.sh ./
RUN pip install -r requirements.txt && FACE_PYTHON=python3.12 bash setup-face-mesh.sh
RUN mkdir -p models && python -c "import urllib.request as u;u.urlretrieve('https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task','models/face_landmarker.task')"
COPY . .
EXPOSE 8770
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8770')+'/health',timeout=4)"
CMD ["python","launch.py"]
