FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (curl for uv, libpq-dev for postgres)
RUN apt-get update && apt-get install -y curl libpq-dev && rm -rf /var/lib/apt/lists/*

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Use system python to avoid venv mapping complexities in Docker
ENV UV_SYSTEM_PYTHON=1

# Install Project dependencies
COPY pyproject.toml ./
RUN uv pip install fastapi uvicorn playwright sqlalchemy aiosqlite pydantic-settings python-dotenv httpx "crawlee[playwright]"

# Install Playwright OS dependencies and chromium
RUN playwright install --with-deps chromium

# Copy rest of backend
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
