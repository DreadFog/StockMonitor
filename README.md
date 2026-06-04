# StockMonitor

StockMonitor is a dockerized Flask web app used by pizzerias to monitor raw product price evolution from supplier invoices in PDF format.

## Features

- PDF parser architecture with parser strategies per supplier.
- Authenticated API using `@require_auth` for protected endpoints.
- Default user pre-seeded from environment variables.
- Products with source name, editable natural name, and latest known unit price.
- Invoices stored in database including original PDF binary and parsed entries.
- Product history and unit price timeline.
- Shopping list/cart with estimated total based on latest product prices.
- Mobile-friendly web interface.

## Run Locally

1. Create env file:

```bash
cp .env.example .env
```

2. Run with Docker:

```bash
docker compose up --build
```

3. Open `http://localhost:5000` and log in with `DEFAULT_USERNAME` / `DEFAULT_PASSWORD` from `.env`.

## API Authentication

1. Get token:

```bash
curl -X POST http://localhost:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

2. Use token:

```bash
curl http://localhost:5000/api/products \
  -H "Authorization: Bearer <token>"
```

## Main API Endpoints

- `POST /api/auth/login`
- `POST /api/invoices/upload` (PDF multipart file)
- `GET /api/invoices`
- `GET /api/invoices/<id>`
- `GET /api/products?q=...`
- `PATCH /api/products/<id>` (rename natural name)
- `GET /api/products/<id>/history`
- `GET /api/cart`
- `POST /api/cart/items`
- `DELETE /api/cart/items/<item_id>`

## Tests

```bash
pytest
```
