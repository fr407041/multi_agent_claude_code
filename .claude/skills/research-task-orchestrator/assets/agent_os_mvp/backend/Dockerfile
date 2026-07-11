FROM node:22-slim AS node_runtime

FROM python:3.12-slim

WORKDIR /app

COPY --from=node_runtime /usr/local /usr/local
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

ENV AGENT_OS_DB_PATH=/app/data/agent_os.db
ENV AGENT_OS_SESSION_ROOT=/sessions

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
