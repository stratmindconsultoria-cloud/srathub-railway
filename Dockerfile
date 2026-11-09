FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/strathub
RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY hub ./hub
COPY sdk ./sdk
COPY apps ./seed_apps
COPY start_railway.py ./start_railway.py
CMD ["python", "start_railway.py"]
