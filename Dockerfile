FROM python:3.11-slim

# Install system dependencies
# ffmpeg: for HLS streaming
# libpq-dev: for PostgreSQL connection
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY . .

# Expose port 8000 for Django
EXPOSE 8000