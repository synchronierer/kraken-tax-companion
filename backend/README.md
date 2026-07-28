# Backend

The backend provides the API and persistence boundaries for Kraken Tax
Companion. It uses FastAPI, SQLAlchemy 2, Alembic, and environment-based
Pydantic settings.

## Run Locally

```bash
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The health endpoint is `GET /health`. It returns HTTP 200 with the application
status and version. Container orchestration uses this endpoint to determine
readiness.

## Database Migrations

```bash
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

The initial migration state contains no application tables.
