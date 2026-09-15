FROM python:3.12-slim

WORKDIR /app

COPY PythonRodagem/requirements-postgres.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt

COPY PythonRodagem/agent_tc_core ./agent_tc_core
COPY PythonRodagem/cli ./cli
COPY PythonRodagem/database ./database
COPY README.md ./

ENV HOST=0.0.0.0
ENV PORT=8000
ENV AGENT_TC_BACKEND=postgres
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "python cli/agent_tc_api.py --backend ${AGENT_TC_BACKEND:-postgres} --host ${HOST} --port ${PORT}"]
