FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy and install dependencies first (optimizes Docker caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Set the Python path so it can find modules inside the lyra folder
ENV PYTHONPATH=/app

# Run the bot
CMD ["python", "lyra/main.py"]
