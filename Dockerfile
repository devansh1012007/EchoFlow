# Start with your chosen Python base image
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory
WORKDIR /app

# CRITICAL SYSTEM DEPENDENCIES
# Update the package list and install ffmpeg and libsndfile1
# The -y flag automatically answers 'yes' to the installation prompts
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    libpq-dev \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/* # Note: rm -rf /var/lib/apt/lists/* is a standard Docker optimization to keep the image size small
# Install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . /app/
