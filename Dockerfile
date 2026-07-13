FROM python:3.12-slim

WORKDIR /app

COPY agent_tc_core ./agent_tc_core
COPY cli ./cli
COPY README.md ./

ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "python cli/agent_tc_api.py --backend supabase --host ${HOST} --port ${PORT}"]
