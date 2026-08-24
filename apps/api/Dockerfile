FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
# Deps-only layer for caching; the stub package satisfies setuptools until the
# real source is copied over it.
RUN mkdir -p superapp && touch superapp/__init__.py && pip install --no-cache-dir -e ".[llm]"
COPY . .
RUN pip install --no-cache-dir -e ".[llm]"
EXPOSE 8000
CMD ["uvicorn", "superapp.main:app", "--host", "0.0.0.0", "--port", "8000"]
