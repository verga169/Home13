# Home13

Home13 è una web app Flask per gestire spese di casa/ristrutturazione, prestiti ricevuti e rimborsi, con export Excel/PDF e assistente AI.

## Funzionalità principali

- Sezioni: `Acquisto casa`, `Ristrutturazione`, `Prestiti ricevuti`, `Rimborsi`.
- Dashboard con grafici totali/timeline e filtro rimborsi per prestatore.
- Formattazione italiana di importi e date.
- Gestione utenti applicativi (solo superadmin, con DB attivo).
- Assistente AI (`/ai-agent-chat`) con function-calling su operazioni CRUD.
- Supporto PWA (manifest + service worker).

## Requisiti

- Python 3.11+ (consigliato 3.11/3.12)
- Dipendenze in `requirements.txt`

## Avvio locale (Windows)

1. Installa dipendenze:

```bash
py -m pip install -r requirements.txt
```

1. Avvia l'app:

```bash
py app.py
```

In alternativa:

```bat
start_app.bat
```

## Variabili ambiente

### Core

- `FLASK_SECRET_KEY`
- `HOME13_SESSION_DAYS` (default `90`)

### Autenticazione

- `HOME13_AUTH_USERNAME`
- Una tra:
  - `HOME13_AUTH_PASSWORD`
  - `HOME13_AUTH_PASSWORD_HASH`

### Database

- `DATABASE_URL` (se assente, fallback su `data_store.json`)

### AI (Gemini)

- `GEMINI_API_KEY`
- `GEMINI_MODEL` (default `gemini-2.5-flash-lite`)
- `HOME13_AI_MAX_HISTORY_TURNS` (default `12`)
- `HOME13_AI_MAX_OUTPUT_TOKENS` (default `380`)
- `HOME13_AI_MAX_ITERATIONS` (default `4`)

## Persistenza dati

- Con `DATABASE_URL`: PostgreSQL (tabelle auto-create: `expenses`, `loans`, `repayments`, `users`).
- Senza `DATABASE_URL`: storage locale su `data_store.json`.

## Export

- `GET /export/excel`: report `.xlsx` con riepilogo e fogli per sezione.
- `GET /export/pdf`: report PDF con metriche e dettaglio movimenti.

## Deploy su Render

- Build command: `pip install -r requirements.txt`
- Start command: `bash render-start.sh`
- `Procfile` e `gunicorn.conf.py` inclusi.
- Health endpoint: `GET /health`

## Sicurezza e accessi

- Route app protette da login.
- Route pubbliche: `GET /health`, asset statici, `GET /sw.js`.
- Login: `GET/POST /login`
- Logout: `POST /logout`
- Admin utenti: `GET /admin/users` (solo superadmin)

## Verifiche rapide (18/03/2026)

- `python -m compileall -q app.py`: OK
- Smoke test Flask test client:
  - `GET /login` -> `200`
  - `GET /health` -> `200`
  - `GET /` senza login -> `302`
- `python -m pytest -q`: non eseguibile perché `pytest` non è installato nell'ambiente corrente.

## Note tecniche

- L'app legge automaticamente `.env.local` e `.env` (se presenti).
- In produzione usare sempre credenziali dedicate e `FLASK_SECRET_KEY` robusta.
