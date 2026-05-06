# BRFN Docker Setup

## Architecture

This project uses a single Docker Compose stack with three services:

- `web`: the Django marketplace application
- `db`: PostgreSQL only
- `payments`: a small FastAPI microservice for mock payment, commission, and settlement endpoints

The Django app can reach the payments service over the internal Compose network at:

`http://payments:8001`

## Run

1. Create your environment file:

```bash
cp .env.example .env
```

Optional weather card: add an OpenWeather key to `.env` if you want product
pages to show current local weather:

```bash
WEATHER_API_KEY=your-openweather-key
WEATHER_LOCATION=Bristol,UK
```

2. Build and start the stack:

```bash
docker compose up --build
```

3. Apply Django migrations:

```bash
docker compose run --rm web python src/manage.py migrate
```

4. Run Django checks and tests:

```bash
docker compose run --rm web python src/manage.py check
docker compose run --rm web python src/manage.py makemigrations --check --dry-run
docker compose run --rm web python src/manage.py test
```

The test command uses the configured app-label test runner so it discovers the
marketplace tests under `src/`.

## Service URLs

- Django web app: `http://localhost:8000`
- Payments API health: `http://localhost:8001/api/payments/health`

## Safe Cleanup Commands

Use these when cleaning up duplicate old stacks or containers:

```bash
docker compose down
docker ps -a
docker compose up --build
```

What they do:

- `docker compose down`: stops and removes the current Compose stack
- `docker ps -a`: shows old containers so you can spot duplicates
- `docker compose up --build`: rebuilds and starts the clean 3-service stack

Because this Compose file does not hardcode `container_name`, it is less likely to clash with accidental duplicate projects.
