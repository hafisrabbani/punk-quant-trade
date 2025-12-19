FROM python:3.11-slim

# =========================
# System dependencies
# =========================
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    gcc \
    g++ \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Working directory
# =========================
WORKDIR /app

# =========================
# Upgrade pip
# =========================
RUN pip install --upgrade pip

# =========================
# Python dependencies
# =========================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# =========================
# Copy source code
# =========================
COPY . .

# =========================
# Environment
# =========================
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg

# =========================
# Run bot
# =========================
CMD ["python", "main.py"]
