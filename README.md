# Home13

Home13 e una web app Flask per monitorare i costi legati a casa e ristrutturazione, con tracciamento di prestiti/rimborsi e report export.

## Funzionalita

- Sezioni separate per:
	- `Acquisto casa`
	- `Ristrutturazione`
	- `Prestiti ricevuti`
	- `Rimborsi`
- Dashboard con 2 grafici:
	- `Totali per categoria` (zoom disabilitato)
	- `Andamento operazioni` (zoom pinch a 2 dita su touch, timeframe 1Y/6M/3M/1M)
- Filtri rimborsi per prestatore con sezione dedicata `Storico Rimborsi`.
- Formattazione numeri italiana (migliaia `.` e decimali `,`).
- Date mostrate in formato italiano (`gg/mm/aaaa`).
- UI responsive desktop/mobile con attenzione a overflow e touch interactions.
- Dopo submit form (aggiunta/modifica/eliminazione) la pagina mantiene la posizione di scroll.
- Favicon da `static/favicon.ico`.
- Installabile come PWA su smartphone (manifest + service worker).

## Test List (Eseguita il 10/03/2026)

### Controlli automatici

- `python -m pytest -q`: nessun test presente nel repository.
- `python -m compileall -q app.py`: OK (nessun errore di sintassi).
- Analisi editor (`Problems`): nessun errore rilevato.
- Verifica import inutilizzati su `app.py` (`source.unusedImports`): nessuna rimozione necessaria.

### Smoke test funzionali (Flask test client)

- `GET /login` -> `200`
- `GET /` senza autenticazione -> `302` redirect login
- `GET /` con sessione autenticata -> `200`
- `GET /health` -> `200`
- `GET /export/excel` -> `200` (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`)
- `GET /export/pdf` -> `200` (`application/pdf`)

### Verifiche UI/manuali eseguite

- Tabelle prestiti/rimborsi riallineate (colonne e azioni coerenti).
- Bottoni modifica/elimina mobile resi quadrati e con dimensioni uniformi.
- Grafico `Totali per categoria`: reset zoom rimosso e interazioni zoom/pan disabilitate.
- Grafico `Andamento operazioni`: zoom touch consentito solo con gesto pinch a due dita.

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
	- `GEMINI_MODEL=gemini-2.5-flash-lite` (opzionale, default)
- Comportamento:
	- usa sempre Gemini per interpretare i comandi (nessun fallback locale)
	- supporta inserimento e rimozione movimenti per: rimborsi, prestiti, acquisto casa, ristrutturazione
	- per le rimozioni richiede conferma esplicita (`si`/`no`) prima di eliminare
	- in caso di ambiguita chiede dettagli aggiuntivi (es. importo/data/soggetto)
	- in UI mostra stato di elaborazione (busy indicator) dopo invio comando
	- errori API Gemini mostrati in modo esplicito (es. quota 429, key/permessi 401/403, timeout rete)

## Login e Sicurezza

- L'app richiede autenticazione per tutte le route applicative.
- Route pubbliche senza login: `GET /health`, asset statici, `GET /sw.js`.
- Endpoint login: `GET/POST /login`
- Logout: `POST /logout`
- Pagina gestione utenti (solo superadmin): `GET /admin/users`
- Ruoli:
	- `superadmin`:
		- in locale: username `admin` (bootstrap), puo gestire utenze applicative.
		- su Render deploy: utente definito da variabili ambiente (`HOME13_AUTH_USERNAME` + password/hash), puo gestire utenze applicative.
	- `user`: utente salvato su tabella DB `users`, puo usare l'app ma non vede la sezione gestione utenze.

Variabili ambiente consigliate:

- `FLASK_SECRET_KEY=<chiave_random_lunga>`
- `HOME13_AUTH_USERNAME=<username_login>`
- una delle due password:
	- `HOME13_AUTH_PASSWORD=<password_plaintext>`
	- `HOME13_AUTH_PASSWORD_HASH=<hash_werkzeug_pbkdf2>`

Note:

- Se non imposti la password, fallback locale: `admin` (da usare solo per bootstrap).
- In produzione imposta sempre `FLASK_SECRET_KEY` e credenziali dedicate.
- Con database attivo, il superadmin puo creare/aggiornare/eliminare utenze dalla pagina dedicata `Gestione Utenze`.
- Le password degli utenti DB sono salvate hashate (Werkzeug PBKDF2).
- La sessione login e persistente anche dopo chiusura browser (utile su mobile) fino a `POST /logout` oppure scadenza cookie.
- Scadenza sessione configurabile con `HOME13_SESSION_DAYS` (default `90`).

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
- Start command: `bash render-start.sh`
- Variabili ambiente minime consigliate:
	- `DATABASE_URL=<url_neon>`
	- `FLASK_SECRET_KEY=<chiave_random_lunga>`
	- `HOME13_AUTH_USERNAME=<username_login>`
	- `HOME13_AUTH_PASSWORD=<password_login>`

Repository include anche:
- `Procfile` con entry web su Gunicorn
- `render-start.sh` (avvio robusto: prova Gunicorn, fallback automatico a `python app.py`)
- `runtime.txt` (`python-3.11.10`) per fissare una versione Python compatibile

L'app include `gunicorn.conf.py` per bind su `0.0.0.0:$PORT`.

### Troubleshooting Porta su Render

Se vedi `No open HTTP ports detected on 0.0.0.0`, verifica:

- Tipo servizio: `Web Service` (non `Background Worker`)
- Start command effettivo: `gunicorn --config gunicorn.conf.py app:app`
- Python runtime: `3.11.x` (coerente con `runtime.txt`)
- Health check path: `/health`

Il repository include anche un `Procfile` con start command web esplicito per evitare configurazioni ambigue.

### Troubleshooting Installazione PWA su Mobile

Se il popup di installazione compare ma l'app non viene installata:

- apri il sito nel browser principale del dispositivo (Chrome su Android, Safari su iOS), non dentro app come Instagram/Facebook/Teams
- verifica che il dominio sia HTTPS (su Render lo e)
- su Android prova anche da menu browser `Installa app`
- su iOS usa `Condividi -> Aggiungi alla schermata Home`

## Dipendenze Principali

- `flask`
- `gunicorn` (non-Windows)
- `psycopg[binary]`
- `openpyxl`
- `reportlab`
