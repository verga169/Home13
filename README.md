# Home13

Applicazione Flask per tracciare:
- spese di acquisto casa (notaio, agenzia, mediatore, ecc.);
- spese di ristrutturazione;
- prestiti ricevuti e rimborsi effettuati.

Include:
- dashboard con grafico mensile aggregato;
- riepilogo economico completo;
- export in Excel e PDF di tutte le voci registrate;
- saldo residuo per prestatore.

## Avvio locale (Windows)

1. Installare dipendenze:

```bash
py -m pip install -r requirements.txt
```

2. Avviare l'app:

```bash
py app.py
```

Oppure:

```bat
start_app.bat
```

3. Aprire il browser su:

```text
http://127.0.0.1:5000/
```

Se usi `start_app.bat`, la porta impostata e `5001`.

## Dati

Se imposti la variabile ambiente `DATABASE_URL` (es. Neon PostgreSQL), l'app salva e legge i dati dal database.

Se `DATABASE_URL` non e impostata, l'app usa il file locale `data_store.json`.

Al primo avvio con database vuoto, eventuali dati locali presenti in `data_store.json` vengono importati automaticamente.

## Export

- `GET /export/excel` genera un file `.xlsx` con fogli separati per sezione.
- `GET /export/pdf` genera un report PDF con riepilogo e dettaglio voci.
