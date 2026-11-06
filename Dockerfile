FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev


# Die REST-API (Dienst `api`; die Migrationen wendet der Dienst `app` an, auf
# den `api` wartet). Ein gebauter Web-Client wird nur ausgeliefert, wenn er
# unter frontend/dist oder GUIDEOS_FRONTEND_DIST liegt.
FROM base AS api
COPY guideos ./guideos
COPY domain_packs ./domain_packs
CMD ["uv", "run", "--no-sync", "uvicorn", "guideos.adapters.fastapi:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]


# Die Streamlit-App (Standardziel; ohne Werkzeug-Server).
FROM base AS app
COPY app.py ./
COPY guideos ./guideos
COPY domain_packs ./domain_packs

CMD ["sh", "-c", "uv run --no-sync python -m guideos.assessment.migrations && uv run --no-sync streamlit run app.py --server.address 0.0.0.0 --server.port 8501"]
