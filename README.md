# Home13

Home13 e una web app Flask per monitorare i costi legati a casa e ristrutturazione, con tracciamento di prestiti/rimborsi e report export.

## Funzionalita

- Sezioni separate per:
	- `Acquisto casa`
	- `Ristrutturazione`
	- `Prestiti ricevuti`
	- `Rimborsi`
- Dashboard con 2 grafici:
	- `Totali per categoria`
	- `Andamento operazioni` (zoom/pan, timeframe 1Y/6M/3M/1M)
- Filtri rimborsi per prestatore con sezione dedicata `Storico Rimborsi`.
- Formattazione numeri italiana (migliaia `.` e decimali `,`).
- Date mostrate in formato italiano (`gg/mm/aaaa`).
- UI responsive desktop/mobile con attenzione a overflow e touch interactions.
- Favicon da `static/favicon.ico`.
- Installabile come PWA su smartphone (manifest + service worker).

## Persistenza Dati

### Produzione (Render + Neon)

Quando `DATABASE_URL` e impostata, l'app usa PostgreSQL (Neon) come storage principale.

Tabelle gestite automaticamente all'avvio:
- `expenses`
- `loans`
- `repayments`

### Locale

- Se `DATABASE_URL` e presente (anche in `.env.local`), l'app usa PostgreSQL.
- Se `DATABASE_URL` non e presente, usa il file locale `data_store.json`.

L'app carica automaticamente variabili da `.env.local` e `.env` (se presenti).

Esempio `.env.local`:

```env
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

## Export

- `GET /export/excel`
	- export `.xlsx` con fogli separati per sezione e riepilogo.
	- colonne importi formattate come numeri monetari (`#,##0.00`) per visualizzazione con separatori locali (es. `1.234,56` in locale italiano).
- `GET /export/pdf`
	- report PDF con layout premium/minimal: header/footer paginati, metric cards, sintesi categorie e tabelle dettagliate.

## PWA (Installazione Smartphone)

- Manifest statico: `GET /static/site.webmanifest`
- Service worker: `static/sw.js` registrato su scope `/`
- Endpoint service worker: `GET /sw.js`
- Health check: `GET /health`

## Assistente AI (Gemini Flash)

- Endpoint: `POST /ai-command`
- Configurazione via env:
	- `GEMINI_API_KEY=<chiave_api_gemini>`
	- `GEMINI_MODEL=gemini-2.5-flash` (opzionale, default)
- Comportamento:
	- usa sempre Gemini per interpretare i comandi (nessun fallback locale)
	- supporta inserimento e rimozione movimenti per: rimborsi, prestiti, acquisto casa, ristrutturazione
	- per le rimozioni richiede conferma esplicita (`si`/`no`) prima di eliminare
	- in caso di ambiguita chiede dettagli aggiuntivi (es. importo/data/soggetto)
	- in UI mostra stato di elaborazione (busy indicator) dopo invio comando
	- errori API Gemini mostrati in modo esplicito (es. quota 429, key/permessi 401/403, timeout rete)

## Avvio Locale (Windows)

1. Installa dipendenze:

```bash
py -m pip install -r requirements.txt
```

2. Avvia l'app:

```bash
py app.py
```

oppure con script:

```bat
start_app.bat
```

`start_app.bat`:
- verifica/install dipendenze mancanti
- sceglie una porta libera tra `5000` e `5010`
- apre browser automaticamente su `http://127.0.0.1:<PORT>/`

## Deploy su Render

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn --config gunicorn.conf.py app:app`
- Variabili ambiente minime consigliate:
	- `DATABASE_URL=<url_neon>`

L'app include `gunicorn.conf.py` per bind su `0.0.0.0:$PORT`.

### Troubleshooting Porta su Render

Se vedi `No open HTTP ports detected on 0.0.0.0`, verifica:

- Tipo servizio: `Web Service` (non `Background Worker`)
- Start command effettivo: `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 app:app`
- Health check path: `/health`

Il repository include anche un `Procfile` con start command web esplicito per evitare configurazioni ambigue.

## Dipendenze Principali

- `flask`
- `gunicorn` (non-Windows)
- `psycopg[binary]`
- `openpyxl`
- `reportlab`
