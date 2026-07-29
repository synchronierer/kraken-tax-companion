# Kraken Tax Companion

Kraken Tax Companion is an open-source, self-hosted application for building a
transparent and auditable view of Kraken transaction data for tax-oriented
workflows. It is a companion for evidence preparation, not tax advice.

## Motivation

Exchange exports are difficult to inspect over long periods, while tax
decisions require reproducible calculations and a clear provenance trail. The
project prioritizes immutable source data, explicit transformations, and
human-reviewable recommendations.

## Architecture

The repository is a monorepo with a FastAPI backend and a React single-page
application. The backend follows clean-architecture boundaries and exposes an
API before user-interface behavior is added. SQLAlchemy isolates persistence
from SQLite so PostgreSQL can be introduced later. Alembic owns all schema
changes. See [docs/architecture.md](docs/architecture.md) and
[MASTERPLAN.md](MASTERPLAN.md).

## Technologies

- Python, FastAPI, SQLAlchemy, Alembic, Pydantic Settings, and Uvicorn
- React, TypeScript, Vite, Material UI, and React Router
- SQLite initially, with PostgreSQL compatibility as an architectural goal
- Docker, Docker Compose, and GitHub Actions

## Quick Start

Requirements: Docker Engine with Docker Compose v2.

```bash
cp .env.example .env
docker compose up --build
```

Open the frontend at <http://localhost:5173>. The API is available at
<http://localhost:8000>, interactive API documentation at
<http://localhost:8000/docs>, and the health check at
<http://localhost:8000/health>.

Expected health response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Stop the stack with `docker compose down`. Named volumes retain database,
logs, and export data.

## Repository Structure

```text
.
├── backend/             FastAPI application, tests, and migrations
├── frontend/            React and TypeScript application
├── docs/                Product and architecture documentation
├── .github/workflows/   Continuous integration
├── docker-compose.yml   Local multi-container stack
└── MASTERPLAN.md        Binding project direction
```

## Development

Run `./dev` as the central entry point for the project-local Codex workflow.
Codex runs in the regular terminal with `--no-alt-screen`, preserving normal
terminal scrollback, mouse-wheel scrolling, and text selection and copying.
Continue a previous session through the interactive `codex resume` selection.
Use `make install` to install local dependencies and `make check` to execute
the same primary checks as CI. See [CONTRIBUTING.md](CONTRIBUTING.md), the
[Developer Guide](docs/DEVELOPER_GUIDE.md), and
[CODING_RULES.md](CODING_RULES.md). Runtime configuration is read only from
environment variables; [.env.example](.env.example) documents supported
values.

## Roadmap

Development progresses from repository foundations through import, the tax
journal, FIFO calculation, recommendations, sales review, Home Assistant
integration, and the 1.0 release. Details are in
[docs/roadmap.md](docs/roadmap.md).

## License

Kraken Tax Companion is licensed under the [MIT License](LICENSE).
