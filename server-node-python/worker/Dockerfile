FROM python:3.12-slim AS base
WORKDIR /app/worker
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

FROM base AS deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

FROM deps AS dev
COPY . .
CMD ["python", "main.py"]

FROM deps AS prod
COPY . .
CMD ["python", "main.py"]
