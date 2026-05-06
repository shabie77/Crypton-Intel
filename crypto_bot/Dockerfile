# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create a data directory for local fallback
RUN mkdir -p /app/data

# Environment variables defaults
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data

# Start the bot
CMD ["python", "main.py"]
