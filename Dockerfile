# Unix Philosophy: Build once, run anywhere
# Economical Law: Use slim image to save space and time

FROM python:3.10-slim

# Set environment variables
# Prevent Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevent Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1
# Set timezone to UTC (matches Binance server time)
ENV TZ=UTC

# Set work directory
WORKDIR /app

# Install system dependencies
# gcc required for some python packages
# tzdata for timezone configuration
RUN apt-get update && apt-get install -y \
    gcc \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies section
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Run as non-root user (Security/Robunteness)
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5000

# Start command
# run.py handles db initialization internally (db.create_all)
CMD ["python", "run.py"]
