FROM python:3.11-slim

WORKDIR /app

# Copy and install dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Create a dedicated directory for the database outside the code context
RUN mkdir -p /app/data

ENV PYTHONPATH=/app

CMD ["python", "lyra/main.py"]
