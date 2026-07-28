# Health

Provides the unauthenticated `GET /health` liveness endpoint used by operators,
containers, and monitoring. A healthy response is HTTP 200 and contains the
status `ok` and current application version.
