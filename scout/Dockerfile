FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
WORKDIR /app
RUN pip install --no-cache-dir playwright==1.49.1 anthropic httpx aiohttp
COPY scout.py .
CMD ["python", "-u", "scout.py"]
