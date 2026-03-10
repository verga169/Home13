import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
import unicodedata
from uuid import uuid4

from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data_store.json")


def load_local_env() -> None:
    """Load basic KEY=VALUE pairs from .env/.env.local if env vars are missing."""
    for filename in (".env.local", ".env"):
        env_path = os.path.join(BASE_DIR, filename)
        if not os.path.exists(env_path):
            continue

        with open(env_path, "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value


load_local_env()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
USE_DATABASE = bool(DATABASE_URL)
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()

AUTH_USERNAME = (os.environ.get("HOME13_AUTH_USERNAME") or "admin").strip()
AUTH_PASSWORD = os.environ.get("HOME13_AUTH_PASSWORD")
AUTH_PASSWORD_HASH = (os.environ.get("HOME13_AUTH_PASSWORD_HASH") or "").strip()

app.secret_key = (
    (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    or (os.environ.get("SECRET_KEY") or "").strip()
    or "home13-dev-secret-change-me"
)

DEFAULT_DATA = {
    "expenses": {
        "acquisto_casa": [],
        "ristrutturazione": [],
    },
    "loans": [],
    "repayments": [],
}


def get_gemini_api_key() -> str:
    # Re-read local env files so updates to .env.local are picked up immediately.
    load_local_env()
    return (os.environ.get("GEMINI_API_KEY") or "").strip()


def get_gemini_model() -> str:
    load_local_env()
    return (os.environ.get("GEMINI_MODEL") or GEMINI_MODEL or "gemini-2.5-flash").strip()


def is_authenticated() -> bool:
    return bool(session.get("authenticated"))


def _is_safe_next_path(next_path: str) -> bool:
    target = sanitize_text(next_path)
    return bool(target) and target.startswith("/") and not target.startswith("//")


def verify_login_credentials(username: str, password: str) -> bool:
    if sanitize_text(username) != AUTH_USERNAME:
        return False

    raw_password = password or ""

    if AUTH_PASSWORD_HASH:
        try:
            return check_password_hash(AUTH_PASSWORD_HASH, raw_password)
        except Exception:
            return False

    if AUTH_PASSWORD is not None:
        return raw_password == AUTH_PASSWORD

    # Last-resort local default for first bootstrap. Override via env in production.
    return raw_password == "admin"


def format_euro(value: float) -> str:
    formatted = f"{float(value):,.2f}"
    integer, decimal = formatted.split(".")
    integer = integer.replace(",", ".")
    return f"{integer},{decimal}"


app.jinja_env.filters["euro"] = format_euro


def format_date_it(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw[:10])
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return raw


app.jinja_env.filters["date_it"] = format_date_it


def _asset_mtime(relative_path: str) -> str:
    file_path = os.path.join(BASE_DIR, relative_path)
    try:
        return str(int(os.path.getmtime(file_path)))
    except OSError:
        return "1"


@app.context_processor
def inject_asset_urls() -> dict:
    favicon_v = _asset_mtime(os.path.join("static", "favicon.ico"))
    favicon16_v = _asset_mtime(os.path.join("static", "favicon-16x16.png"))
    favicon32_v = _asset_mtime(os.path.join("static", "favicon-32x32.png"))
    android192_v = _asset_mtime(os.path.join("static", "android-chrome-192x192.png"))
    android512_v = _asset_mtime(os.path.join("static", "android-chrome-512x512.png"))
    apple_touch_v = _asset_mtime(os.path.join("static", "apple-touch-icon.png"))
    manifest_v = _asset_mtime(os.path.join("static", "site.webmanifest"))
    sw_v = _asset_mtime(os.path.join("static", "sw.js"))
    return {
        "favicon_ico_url": url_for("static", filename="favicon.ico", v=favicon_v),
        "favicon_16_url": url_for("static", filename="favicon-16x16.png", v=favicon16_v),
        "favicon_32_url": url_for("static", filename="favicon-32x32.png", v=favicon32_v),
        "android_192_url": url_for("static", filename="android-chrome-192x192.png", v=android192_v),
        "android_512_url": url_for("static", filename="android-chrome-512x512.png", v=android512_v),
        "apple_touch_icon_url": url_for("static", filename="apple-touch-icon.png", v=apple_touch_v),
        "manifest_url": url_for("static", filename="site.webmanifest", v=manifest_v),
        "sw_url": url_for("service_worker", v=sw_v),
    }


def parse_amount(raw_value: str) -> float:
    raw = (raw_value or "").strip().replace(" ", "")
    if not raw:
        raise ValueError("Importo mancante")

    has_dot = "." in raw
    has_comma = "," in raw

    if has_dot and has_comma:
        # Italian style: 1.234,56
        normalized = raw.replace(".", "").replace(",", ".")
    elif has_comma:
        # Decimal comma: 1234,56
        normalized = raw.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        # Thousands only with dots: 1.234.567
        normalized = raw.replace(".", "")
    else:
        # Plain or dot-decimal input: 1234 or 1234.56
        normalized = raw

    value = float(normalized)
    if value <= 0:
        raise ValueError("Importo deve essere maggiore di zero")
    return value


def sanitize_text(raw_value: str) -> str:
    return (raw_value or "").strip()


def normalize_text(raw_value: str) -> str:
    text = sanitize_text(raw_value).lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip()


def parse_iso_date(raw_value: str) -> date:
    raw = sanitize_text(raw_value)
    if not raw:
        return date.today()
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError as exc:
        raise ValueError("Data non valida") from exc


def parse_date_from_text(raw_value: str) -> str | None:
    text = normalize_text(raw_value)
    if not text:
        return None

    today = date.today()
    month_map = {
        "gennaio": 1,
        "gen": 1,
        "febbraio": 2,
        "feb": 2,
        "marzo": 3,
        "mar": 3,
        "aprile": 4,
        "apr": 4,
        "maggio": 5,
        "mag": 5,
        "giugno": 6,
        "giu": 6,
        "luglio": 7,
        "lug": 7,
        "agosto": 8,
        "ago": 8,
        "settembre": 9,
        "set": 9,
        "ottobre": 10,
        "ott": 10,
        "novembre": 11,
        "nov": 11,
        "dicembre": 12,
        "dic": 12,
    }
    weekday_map = {
        "lunedi": 0,
        "martedi": 1,
        "mercoledi": 2,
        "giovedi": 3,
        "venerdi": 4,
        "sabato": 5,
        "domenica": 6,
    }

    def safe_date(year_value: int, month_value: int, day_value: int) -> str | None:
        try:
            return date(year_value, month_value, day_value).isoformat()
        except ValueError:
            return None

    def next_weekday(target_weekday: int, force_next_week: bool = False) -> str:
        delta = (target_weekday - today.weekday()) % 7
        if delta == 0 or force_next_week:
            delta += 7
        return (today + timedelta(days=delta)).isoformat()

    if "dopodomani" in text:
        return (today + timedelta(days=2)).isoformat()
    if "l altro ieri" in text or "altro ieri" in text:
        return (today - timedelta(days=2)).isoformat()
    if "oggi" in text:
        return today.isoformat()
    if "domani" in text:
        return (today + timedelta(days=1)).isoformat()
    if "ieri" in text:
        return (today - timedelta(days=1)).isoformat()

    relative_match = re.search(r"\b(?:tra|fra)\s+(\d{1,3})\s+(giorni|giorno|settimane|settimana|mesi|mese)\b", text)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit.startswith("giorn"):
            return (today + timedelta(days=amount)).isoformat()
        if unit.startswith("settim"):
            return (today + timedelta(days=amount * 7)).isoformat()
        if unit.startswith("mes"):
            return (today + timedelta(days=amount * 30)).isoformat()

    if "settimana prossima" in text:
        return (today + timedelta(days=7)).isoformat()
    if "settimana scorsa" in text:
        return (today - timedelta(days=7)).isoformat()

    weekday_match = re.search(r"\b(lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica)\b(?:\s+(prossimo|prossima))?", text)
    if weekday_match:
        weekday_name = weekday_match.group(1)
        is_next = bool(weekday_match.group(2))
        return next_weekday(weekday_map[weekday_name], force_next_week=is_next)

    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            return parse_iso_date(iso_match.group(1)).isoformat()
        except ValueError:
            return None

    year_first_match = re.search(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", text)
    if year_first_match:
        year_part, month_part, day_part = year_first_match.groups()
        return safe_date(int(year_part), int(month_part), int(day_part))

    it_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", text)
    if it_match:
        day_part, month_part, year_part = it_match.groups()
        year_int = int(year_part)
        if year_int < 100:
            year_int += 2000
        return safe_date(year_int, int(month_part), int(day_part))

    short_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})\b", text)
    if short_match:
        day_part, month_part = short_match.groups()
        candidate = safe_date(today.year, int(month_part), int(day_part))
        if candidate:
            parsed = datetime.fromisoformat(candidate).date()
            if parsed < today:
                candidate_next = safe_date(today.year + 1, int(month_part), int(day_part))
                if candidate_next:
                    return candidate_next
            return candidate

    month_name_match = re.search(
        r"\b(\d{1,2})\s+([a-z]+)\s*(\d{2,4})?\b",
        text,
    )
    if month_name_match:
        day_part = int(month_name_match.group(1))
        month_name = month_name_match.group(2)
        if month_name in month_map:
            year_raw = month_name_match.group(3)
            if year_raw:
                year_int = int(year_raw)
                if year_int < 100:
                    year_int += 2000
            else:
                year_int = today.year
            candidate = safe_date(year_int, month_map[month_name], day_part)
            if candidate and not year_raw and datetime.fromisoformat(candidate).date() < today:
                return safe_date(year_int + 1, month_map[month_name], day_part)
            return candidate

    return None


def parse_amount_from_text(raw_value: str) -> float | None:
    text = normalize_text(raw_value)
    if not text:
        return None

    textual_amounts = {
        "mille": 1000.0,
        "duemila": 2000.0,
        "tremila": 3000.0,
        "quattromila": 4000.0,
        "cinquemila": 5000.0,
        "seimila": 6000.0,
        "settemila": 7000.0,
        "ottomila": 8000.0,
        "novemila": 9000.0,
        "diecimila": 10000.0,
    }
    for token, value in textual_amounts.items():
        if re.search(rf"\b{token}\b", text):
            return value

    k_match = re.search(r"\b(\d+(?:[\.,]\d+)?)\s*k\b", text)
    if k_match:
        normalized = k_match.group(1).replace(",", ".")
        try:
            return float(normalized) * 1000
        except ValueError:
            pass

    amount_patterns = [
        r"(?:di|da)\s+([0-9][0-9\.,\s]*)\s*(?:euro|eur)?\b",
        r"([0-9][0-9\.,\s]*)\s*(?:euro|eur)\b",
    ]
    for pattern in amount_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            return parse_amount(match.group(1))
        except ValueError:
            continue

    # Fallback: plain number without "euro" (e.g. "aggiungi rimborso Sandro 1000")
    # Use only values > 31 to avoid confusing day/month numbers with an amount.
    raw_numbers = re.findall(r"\b\d+(?:[\.,]\d+)?\b", text)
    candidates = []
    for raw_number in raw_numbers:
        normalized = raw_number.replace(",", ".")
        try:
            value = float(normalized)
        except ValueError:
            continue
        if value > 31:
            candidates.append(value)
    if candidates:
        return max(candidates)

    return None


def parse_lender_from_text(raw_value: str) -> str | None:
    text = sanitize_text(raw_value)
    if not text:
        return None

    match = re.search(
        r"\b(?:a|da)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' ]{1,40}?)(?=\s+\b(?:di|del|il|in|per)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        quoted = re.search(r'"([^\"]{2,40})"', text)
        if quoted:
            return sanitize_text(quoted.group(1)) or None
        # fallback: after common command verbs, keep first meaningful chunk
        fallback = re.search(
            r"\b(?:rimborso|rimborsa|rimborsare|prestito|presta|aggiungi)\b\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' ]{1,40})",
            text,
            flags=re.IGNORECASE,
        )
        if fallback:
            candidate = sanitize_text(re.split(r"\b(di|del|in|per|oggi|domani|ieri|euro)\b", fallback.group(1), maxsplit=1)[0])
            return candidate or None
        return None
    lender = sanitize_text(match.group(1))
    return lender or None


def normalize_expense_description(raw_value: str) -> str | None:
    candidate = sanitize_text(raw_value)
    if not candidate:
        return None

    candidate = candidate.strip("\"'")
    candidate = re.sub(r"^[\s,;:\-]+|[\s,;:\-]+$", "", candidate)
    candidate = re.sub(r"^(?:uno|una|un|il|lo|la|i|gli|le)\b\s+", "", candidate, flags=re.IGNORECASE)
    candidate = sanitize_text(candidate)
    normalized = normalize_text(candidate)

    if not normalized or len(normalized) < 2:
        return None
    if not re.search(r"[a-zA-ZÀ-ÿ]", candidate):
        return None

    invalid_exact = {
        "spesa",
        "spese",
        "voce",
        "descrizione",
        "ristrutturazione",
        "acquisto",
        "acquisto casa",
        "casa",
        "categoria",
        "alla",
        "al",
        "a",
        "di",
        "da",
        "per",
        "in",
        "nel",
        "nella",
    }
    if normalized in invalid_exact:
        return None

    if re.match(r"^(?:di|da|per|a|al|alla|in|nel|nella|categoria)\b", normalized):
        return None

    if re.fullmatch(r"(?:alla|al|in|nella|nel|per\s+la)\s+categoria\s+.+", normalized):
        return None
    if normalized.startswith("categoria "):
        return None
    if re.search(r"\beuro\b", normalized):
        return None
    if re.fullmatch(r"\d+[\d\s\.,]*", normalized):
        return None

    # Reject command-like fragments that are not real expense object names.
    if re.search(r"\b(aggiungi|inserisci|registra|segna|spesa|categoria)\b", normalized) and len(normalized.split()) <= 5:
        return None

    return candidate


def parse_expense_description_from_text(raw_value: str) -> str | None:
    text = sanitize_text(raw_value)
    if not text:
        return None

    explicit = re.search(
        r"\b(?:voce|descrizione)\s+(.+?)(?=\s+\b(?:di|del|il|in data|oggi|domani|ieri)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if explicit:
        description = normalize_expense_description(explicit.group(1))
        return description or None

    quoted = re.search(r'"([^\"]{2,80})"', text)
    if quoted:
        return normalize_expense_description(quoted.group(1))

    patterns = [
        # "ho comprato una tv da 1000 euro"
        r"\b(?:ho\s+)?(?:comprat[oaie]|acquistat[oaie]|pres[oaie]|ordinat[oaie])\s+"
        r"(?:uno|una|un|il|lo|la|i|gli|le)?\b\s*"
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'\- ]{1,80}?)(?=\s+\b(?:da|di|per|a|in|alla|al|nel|nella|su|con)\b|[\,\.;]|$)",
        # "ho speso 200 euro per parquet"
        r"\b(?:ho\s+)?(?:spes[oaie]|pagat[oaie])\b[^\n\r]{0,80}?\bper\s+"
        r"([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'\- ]{1,80}?)(?=\s+\b(?:di|in|alla|al|nel|nella|oggi|domani|ieri)\b|[\,\.;]|$)",
        # "ho pagato 1200 euro al notaio"
        r"\b(?:ho\s+)?pagat[oaie]\b[^\n\r]{0,40}?\b(?:al|alla|allo|ai|alle|a)\s+"
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'\- ]{1,80}?)(?=\s+\b(?:per|di|in|alla|al|nel|nella|oggi|domani|ieri)\b|[\,\.;]|$)",
        # "aggiungi spesa parquet di 500"
        r"\b(?:aggiungi|inserisci|registra|segna)\s+(?:la\s+)?(?:spesa\s+)?(?:voce\s+)?"
        r"([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9'\- ]{1,80}?)(?=\s+\b(?:di|da|in data|oggi|domani|ieri|alla|al|nel|nella|categoria|euro)\b|[\,\.;]|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        description = normalize_expense_description(match.group(1))
        if description:
            return description

    generic = re.search(
        r"\b(?:spesa|acquisto|ristrutturazione)\b\s+(?:voce\s+)?(.+?)(?=\s+\b(?:di|del|in data|oggi|domani|ieri|euro)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if generic:
        description = normalize_expense_description(generic.group(1))
        if description:
            return description
    return None


def detect_ai_intent(raw_value: str) -> str | None:
    text = normalize_text(raw_value)
    if not text:
        return None

    house_purchase_pattern = (
        r"\b("
        r"acquisto\s+casa|acquisto\s+immobile|compr[a-z]*\s+casa|compr[a-z]*\s+immobile|"
        r"rogito|notaio|agenzia\s+immobiliare|compromesso|caparra|mutuo|atto\s+di\s+vendita|"
        r"venditore|compravendita|imposta\s+di\s+registro"
        r")\b"
    )

    if re.search(r"\b(rimuovi|elimina|cancella|delete|togli)\b", text):
        if re.search(r"\b(rimborso|rimborsi)\b", text):
            return "delete_repayment"
        if re.search(r"\b(prestito|prestiti)\b", text):
            return "delete_loan"
        if re.search(r"\b(ristrutturazione|ristrutturare|ristruttura)\b", text):
            return "delete_expense_ristrutturazione"
        if re.search(house_purchase_pattern, text):
            return "delete_expense_acquisto_casa"
        if re.search(r"\b(acquisto|acquistat[oaie]|comprat[oaie]|ho\s+comprato|ho\s+acquistato)\b", text):
            return "delete_expense_ristrutturazione"
        return "delete_expense_acquisto_casa"
    if re.search(r"\b(rimborso|rimborsa|rimborsare|restituisco|restituzione)\b", text):
        return "add_repayment"
    if re.search(r"\b(prestito|prestare|ricevuto da|mi ha prestato)\b", text):
        return "add_loan"
    if re.search(house_purchase_pattern, text):
        return "add_expense_acquisto_casa"
    if re.search(r"\b(ristrutturazione|ristrutturare|ristruttura)\b", text):
        return "add_expense_ristrutturazione"
    if re.search(r"\b(acquisto|acquistat[oaie]|comprat[oaie]|ho\s+comprato|ho\s+acquistato)\b", text):
        return "add_expense_ristrutturazione"
    return None


def normalize_ai_intent(raw_intent: str | None) -> str | None:
    intent = normalize_text(raw_intent)
    if not intent:
        return None

    aliases = {
        "add_repayment": "add_repayment",
        "delete_repayment": "delete_repayment",
        "rimborso": "add_repayment",
        "repayment": "add_repayment",
        "remove_repayment": "delete_repayment",
        "add_loan": "add_loan",
        "delete_loan": "delete_loan",
        "prestito": "add_loan",
        "loan": "add_loan",
        "remove_loan": "delete_loan",
        "add_expense_acquisto_casa": "add_expense_acquisto_casa",
        "delete_expense_acquisto_casa": "delete_expense_acquisto_casa",
        "acquisto_casa": "add_expense_acquisto_casa",
        "expense_acquisto": "add_expense_acquisto_casa",
        "remove_expense_acquisto": "delete_expense_acquisto_casa",
        "add_expense_ristrutturazione": "add_expense_ristrutturazione",
        "delete_expense_ristrutturazione": "delete_expense_ristrutturazione",
        "ristrutturazione": "add_expense_ristrutturazione",
        "expense_ristrutturazione": "add_expense_ristrutturazione",
        "remove_expense_ristrutturazione": "delete_expense_ristrutturazione",
    }
    return aliases.get(intent)


def extract_json_object(raw_text: str) -> dict | None:
    text = sanitize_text(raw_text)
    if not text:
        return None

    # Direct JSON first.
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    # Markdown fenced JSON fallback.
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

    # Last-resort: first JSON-like object.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def parse_slots_from_gemini(intent: str, llm_slots: dict) -> dict:
    slots: dict = {}
    if not isinstance(llm_slots, dict):
        return slots

    if intent in {"add_repayment", "add_loan", "delete_repayment", "delete_loan"}:
        lender = sanitize_text(llm_slots.get("lender"))
        if lender:
            slots["lender"] = lender
    else:
        description = normalize_expense_description(llm_slots.get("description"))
        if description:
            slots["description"] = description

    amount_raw = llm_slots.get("amount")
    if isinstance(amount_raw, (int, float)):
        if float(amount_raw) > 0:
            slots["amount"] = float(amount_raw)
    elif isinstance(amount_raw, str):
        parsed_amount = parse_amount_from_text(amount_raw)
        if parsed_amount is not None:
            slots["amount"] = parsed_amount

    date_raw = llm_slots.get("date")
    if isinstance(date_raw, str):
        parsed_date = parse_date_from_text(date_raw)
        if parsed_date:
            slots["date"] = parsed_date
        else:
            try:
                slots["date"] = parse_iso_date(date_raw).isoformat()
            except ValueError:
                pass

    item_id_raw = llm_slots.get("id")
    if isinstance(item_id_raw, str):
        item_id = sanitize_text(item_id_raw)
        if item_id:
            slots["id"] = item_id

    confirm_raw = llm_slots.get("confirm")
    if isinstance(confirm_raw, bool):
        slots["confirm"] = confirm_raw
    elif isinstance(confirm_raw, str):
        normalized_confirm = normalize_text(confirm_raw)
        if normalized_confirm in {"si", "s", "ok", "confermo", "yes", "y"}:
            slots["confirm"] = True
        elif normalized_confirm in {"no", "n", "annulla", "stop", "cancel"}:
            slots["confirm"] = False

    return slots


def parse_with_gemini(message: str, pending: dict | None) -> dict:
    api_key = get_gemini_api_key()
    if not api_key:
        return {"ok": False, "reply": "GEMINI_API_KEY non configurata."}

    model = get_gemini_model()

    pending_payload = pending if isinstance(pending, dict) else {}
    prompt = (
        "Interpreta il comando utente per un'app di finanza domestica. "
        "Rispondi SOLO con JSON valido (nessun testo extra). "
        "Schema: "
        "{\"intent\":string|null,\"reply\":string,\"slots\":{\"lender\":string|null,\"description\":string|null,\"amount\":number|string|null,\"date\":string|null,\"id\":string|null,\"confirm\":boolean|null}}. "
        "Intent consentiti: add_repayment, add_loan, add_expense_acquisto_casa, add_expense_ristrutturazione, delete_repayment, delete_loan, delete_expense_acquisto_casa, delete_expense_ristrutturazione. "
        "Classificazione categorie spesa: add_expense_acquisto_casa SOLO per costi di compravendita immobile (rogito, notaio, agenzia immobiliare, imposte, caparra, mutuo). "
        "Per acquisti di oggetti/materiali/lavori (es. TV, parquet, mobili, idraulico) usa add_expense_ristrutturazione. "
        "Nel campo reply non usare mai i nomi tecnici degli intent (es: add_repayment), usa italiano naturale. "
        "Per le spese: se la voce/oggetto non e chiaramente identificabile, imposta slots.description=null (non inventare descrizioni e non usare frasi tipo 'alla categoria ...'). "
        "Se il messaggio non richiede una registrazione di movimento, metti intent=null e fornisci reply utile e concisa in italiano. "
        "Date ammesse in output: ISO yyyy-mm-dd se certa, altrimenti stringa originale da cui dedurre. "
        f"Stato pending corrente: {json.dumps(pending_payload, ensure_ascii=False)}. "
        f"Messaggio utente: {message}"
    )

    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        status_text = ""
        message_text = ""
        try:
            payload = json.loads(body)
            error_obj = payload.get("error", {}) if isinstance(payload, dict) else {}
            status_text = sanitize_text(error_obj.get("status"))
            message_text = sanitize_text(error_obj.get("message"))
        except Exception:
            pass

        if status_code == 429:
            return {
                "ok": False,
                "reply": "Quota Gemini esaurita (HTTP 429). Verifica billing e limiti API nel progetto Google AI Studio/Google Cloud, poi riprova.",
            }
        if status_code in {401, 403}:
            return {
                "ok": False,
                "reply": "Accesso Gemini negato (API key non valida o senza permessi). Controlla GEMINI_API_KEY e abilitazione API.",
            }

        extra = f" ({status_text})" if status_text else ""
        return {
            "ok": False,
            "reply": f"Errore Gemini HTTP {status_code}{extra}. {message_text or 'Riprova tra poco.'}",
        }
    except (urllib.error.URLError, TimeoutError):
        return {
            "ok": False,
            "reply": "Connessione a Gemini non riuscita (rete/timeout). Riprova tra poco.",
        }

    try:
        payload = json.loads(raw)
    except Exception:
        return {"ok": False, "reply": "Risposta Gemini non valida (JSON non parsabile)."}

    text_parts: list[str] = []
    for candidate in payload.get("candidates", []):
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts", []) if isinstance(content, dict) else []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])

    llm_obj = extract_json_object("\n".join(text_parts))
    if not llm_obj:
        return {"ok": False, "reply": "Risposta Gemini non interpretabile. Riprova con una frase piu diretta."}

    intent = normalize_ai_intent(llm_obj.get("intent"))
    slots = parse_slots_from_gemini(intent, llm_obj.get("slots", {})) if intent else {}
    reply = sanitize_text(llm_obj.get("reply"))
    return {"ok": True, "intent": intent, "slots": slots, "reply": reply}


def required_slots_for_intent(intent: str) -> list[str]:
    if intent in {
        "delete_repayment",
        "delete_loan",
        "delete_expense_acquisto_casa",
        "delete_expense_ristrutturazione",
    }:
        return []
    if intent in {"add_repayment", "add_loan"}:
        return ["lender", "amount", "date"]
    return ["description", "amount", "date"]


def is_delete_intent(intent: str | None) -> bool:
    return intent in {
        "delete_repayment",
        "delete_loan",
        "delete_expense_acquisto_casa",
        "delete_expense_ristrutturazione",
    }


def is_yes_reply(text: str) -> bool:
    normalized = normalize_text(text)
    return normalized in {"si", "s", "ok", "confermo", "yes", "y", "procedi"}


def is_no_reply(text: str) -> bool:
    normalized = normalize_text(text)
    return normalized in {"no", "n", "annulla", "stop", "cancel", "non confermo"}


def _amount_matches(entry_amount: float, slot_amount: float) -> bool:
    return abs(float(entry_amount) - float(slot_amount)) < 0.01


def find_delete_candidates(intent: str, slots: dict) -> list[dict]:
    data = load_data()

    if intent == "delete_repayment":
        entries = data["repayments"]
        text_key = "lender"
        section = "repayments"
    elif intent == "delete_loan":
        entries = data["loans"]
        text_key = "lender"
        section = "loans"
    elif intent == "delete_expense_ristrutturazione":
        entries = data["expenses"]["ristrutturazione"]
        text_key = "description"
        section = "ristrutturazione"
    else:
        entries = data["expenses"]["acquisto_casa"]
        text_key = "description"
        section = "acquisto_casa"

    slot_id = sanitize_text(slots.get("id"))
    if slot_id:
        for item in entries:
            if sanitize_text(item.get("id")) == slot_id:
                return [{"section": section, "item": item}]
        return []

    slot_amount = slots.get("amount")
    slot_date = sanitize_text(slots.get("date"))
    slot_text = sanitize_text(slots.get("lender") or slots.get("description"))

    candidates = []
    for item in entries:
        if slot_amount is not None and not _amount_matches(item.get("amount", 0.0), slot_amount):
            continue
        if slot_date and sanitize_text(item.get("date")) != slot_date:
            continue
        if slot_text and normalize_text(item.get(text_key, "")) != normalize_text(slot_text):
            continue
        candidates.append({"section": section, "item": item})

    return candidates


def delete_ai_operation(intent: str, item_id: str) -> bool:
    if intent == "delete_repayment":
        section = "repayments"
    elif intent == "delete_loan":
        section = "loans"
    elif intent == "delete_expense_ristrutturazione":
        section = "ristrutturazione"
    else:
        section = "acquisto_casa"

    if USE_DATABASE:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if section in {"acquisto_casa", "ristrutturazione"}:
                    cur.execute("DELETE FROM expenses WHERE id = %s AND category = %s", (item_id, section))
                elif section == "loans":
                    cur.execute("DELETE FROM loans WHERE id = %s", (item_id,))
                else:
                    cur.execute("DELETE FROM repayments WHERE id = %s", (item_id,))
                deleted = cur.rowcount > 0
            if deleted:
                conn.commit()
        return deleted

    data = load_data()
    if section in {"acquisto_casa", "ristrutturazione"}:
        deleted = remove_by_id(data["expenses"][section], item_id)
    elif section == "loans":
        deleted = remove_by_id(data["loans"], item_id)
    else:
        deleted = remove_by_id(data["repayments"], item_id)
    if deleted:
        save_data(data)
    return deleted


def describe_item_for_delete(intent: str, item: dict) -> str:
    amount = format_euro(item.get("amount", 0.0))
    item_date = format_date_it(item.get("date", ""))
    if intent in {"delete_repayment", "delete_loan"}:
        who = item.get("lender", "Sconosciuto")
        kind = "rimborso" if intent == "delete_repayment" else "prestito"
        return f"{kind} di {amount} EUR ({who}, {item_date})"
    return f"spesa '{item.get('description', '')}' di {amount} EUR ({item_date})"


def parse_slots_for_intent(intent: str, raw_value: str) -> dict:
    slots: dict = {}

    date_validation_error = detect_date_validation_error(raw_value)
    if date_validation_error:
        slots["date_error"] = date_validation_error

    parsed_date = parse_date_from_text(raw_value)
    if parsed_date:
        slots["date"] = parsed_date

    parsed_amount = parse_amount_from_text(raw_value)
    if parsed_amount is not None:
        slots["amount"] = parsed_amount

    if intent in {"add_repayment", "add_loan", "delete_repayment", "delete_loan"}:
        lender = parse_lender_from_text(raw_value)
        if lender:
            slots["lender"] = lender
    else:
        description = parse_expense_description_from_text(raw_value)
        if description:
            slots["description"] = description

    return slots


def ask_for_missing_slot(intent: str, missing_slot: str) -> str:
    if missing_slot == "date":
        return "Per quale data vuoi registrarlo? (es: 10/03/2026; accetto anche 2026-03-10)"
    if missing_slot == "amount":
        return "Qual e l'importo esatto in euro?"
    if missing_slot == "lender":
        label = "rimborso" if intent == "add_repayment" else "prestito"
        return f"A quale prestatore devo associare il {label}?"
    if missing_slot == "description":
        category_label = "ristrutturazione" if intent == "add_expense_ristrutturazione" else "acquisto casa"
        return (
            f"Non ho capito bene la voce della spesa ({category_label}). "
            "Come vuoi chiamarla? Esempi: TV, parquet, idraulico, notaio."
        )
    return "Mi serve un dettaglio in piu per completare l'operazione."


def save_ai_operation(intent: str, slots: dict) -> dict:
    if intent == "add_repayment":
        item = {
            "id": new_id(),
            "date": slots["date"],
            "lender": slots["lender"],
            "amount": slots["amount"],
        }
        if USE_DATABASE:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO repayments (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (item["id"], item["date"], item["lender"], item["amount"]),
                    )
                conn.commit()
        else:
            data = load_data()
            data["repayments"].append(item)
            save_data(data)
        return item

    if intent == "add_loan":
        item = {
            "id": new_id(),
            "date": slots["date"],
            "lender": slots["lender"],
            "amount": slots["amount"],
        }
        if USE_DATABASE:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO loans (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (item["id"], item["date"], item["lender"], item["amount"]),
                    )
                conn.commit()
        else:
            data = load_data()
            data["loans"].append(item)
            save_data(data)
        return item

    expense_category = "acquisto_casa" if intent == "add_expense_acquisto_casa" else "ristrutturazione"
    item = {
        "id": new_id(),
        "date": slots["date"],
        "description": slots["description"],
        "amount": slots["amount"],
    }
    if USE_DATABASE:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO expenses (id, category, operation_date, description, amount)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (item["id"], expense_category, item["date"], item["description"], item["amount"]),
                )
            conn.commit()
    else:
        data = load_data()
        data["expenses"][expense_category].append(item)
        save_data(data)
    return item


def ai_intent_confirmation(intent: str, item: dict) -> str:
    if intent == "add_repayment":
        return (
            f"Rimborso inserito: {format_euro(item['amount'])} EUR a {item['lender']} in data "
            f"{format_date_it(item['date'])}."
        )
    if intent == "add_loan":
        return (
            f"Prestito inserito: {format_euro(item['amount'])} EUR da {item['lender']} in data "
            f"{format_date_it(item['date'])}."
        )
    return (
        f"Spesa inserita: {item['description']} da {format_euro(item['amount'])} EUR in data "
        f"{format_date_it(item['date'])}."
    )


def ai_capabilities_reply() -> str:
    return (
        "Posso aiutarti a registrare e rimuovere movimenti: rimborsi, prestiti ricevuti, "
        "spese di acquisto casa e spese di ristrutturazione. "
        "Se vuoi, scrivimi direttamente una frase come: "
        "'Aggiungi rimborso di 120 euro a Marco oggi' oppure 'Rimuovi il rimborso da 120 euro a Marco'."
    )


def is_capabilities_question(text: str) -> bool:
    normalized = normalize_text(text)
    triggers = [
        "cosa puoi fare",
        "cosa sai fare",
        "che cosa puoi fare",
        "che cosa sai fare",
        "come puoi aiutarmi",
        "in cosa puoi aiutarmi",
        "quali operazioni puoi registrare",
    ]
    return any(trigger in normalized for trigger in triggers)


def detect_date_validation_error(raw_value: str) -> str | None:
    text = normalize_text(raw_value)
    if not text:
        return None

    year_first_match = re.search(r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", text)
    if not year_first_match:
        return None

    year_part, month_part, day_part = year_first_match.groups()
    year_value = int(year_part)
    month_value = int(month_part)
    day_value = int(day_part)

    if month_value < 1 or month_value > 12:
        return "Data non valida: nel formato con anno iniziale (YYYY-MM-DD) il mese deve essere tra 1 e 12."
    if day_value < 1 or day_value > 31:
        return "Data non valida: il giorno deve essere tra 1 e 31."
    try:
        date(year_value, month_value, day_value)
    except ValueError:
        return "Data non valida: combinazione anno/mese/giorno inesatta."

    return None


def local_smalltalk_reply(text: str, pending: dict | None) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    summary_reply = local_summary_reply(text)
    if summary_reply:
        return summary_reply

    if normalized in {"annulla", "stop", "cancel", "esci", "lascia perdere"}:
        if pending:
            return "Operazione annullata."
        return "Non c'e nessuna operazione in corso da annullare."

    if normalized in {"ciao", "salve", "buongiorno", "buonasera", "hey", "ehi"}:
        return "Ciao. Posso aiutarti ad aggiungere o rimuovere movimenti di spese, prestiti e rimborsi."

    if normalized in {"grazie", "ti ringrazio", "thanks", "thank you"}:
        return "Di nulla. Se vuoi, possiamo inserire o rimuovere un'altra operazione."

    if "cosa puoi fare" in normalized or "come funzioni" in normalized:
        return ai_capabilities_reply()

    if re.search(r"\b(modifica|aggiorna|edit)\b", normalized):
        return "Posso aggiungere o rimuovere movimenti via chat. Per modificare una voce esistente usa il tasto modifica nella tabella."

    if re.search(r"\b(mostra|elenca|lista|riepilogo)\b", normalized):
        return "Per ora i riepiloghi si leggono nella dashboard e nelle tabelle qui sotto. Via chat gestisco soprattutto inserimenti e rimozioni."

    return None


def local_summary_reply(text: str) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None

    wants_total = bool(re.search(r"\b(totale|totali|somma|quanto|ammonta|importo|saldo|debito|residuo)\b", normalized))
    if not wants_total:
        return None

    # If user is explicitly asking to perform an action, don't treat it as a summary question.
    if re.search(r"\b(aggiungi|inserisci|registra|rimuovi|elimina|cancella|togli)\b", normalized):
        return None

    summary = build_summary(load_data())

    if "ristruttur" in normalized:
        return f"Il totale della ristrutturazione e {format_euro(summary['ristr_total'])} EUR."

    if re.search(r"\b(acquisto|casa|immobile|rogito|notaio|agenzia)\b", normalized):
        return f"Il totale dell'acquisto casa e {format_euro(summary['acquisto_total'])} EUR."

    if "spese" in normalized:
        return f"Le spese totali sono {format_euro(summary['spese_total'])} EUR."

    if "prestiti" in normalized:
        return f"Il totale dei prestiti ricevuti e {format_euro(summary['loans_total'])} EUR."

    if "rimbors" in normalized:
        return f"Il totale dei rimborsi e {format_euro(summary['repayments_total'])} EUR."

    if "debito" in normalized or "residuo" in normalized:
        return f"Il debito residuo e {format_euro(summary['debito_residuo'])} EUR."

    if "saldo" in normalized:
        lender = parse_lender_from_text(text)
        if lender:
            lender_norm = normalize_text(lender)
            for row in summary["lender_balance"]:
                if normalize_text(row.get("lender", "")) == lender_norm:
                    return f"Il saldo residuo verso {row['lender']} e {format_euro(row['balance'])} EUR."
            return f"Non trovo un prestatore chiamato {lender} nello storico."
        return f"Il debito residuo complessivo e {format_euro(summary['debito_residuo'])} EUR."

    return None


def process_ai_command(message: str, pending: dict | None) -> dict:
    text = sanitize_text(message)
    if not text and not pending:
        raise ValueError("Inserisci un comando testuale.")

    if is_capabilities_question(text):
        return {
            "status": "needs_input",
            "reply": ai_capabilities_reply(),
            "pending": pending if isinstance(pending, dict) else None,
        }

    if isinstance(pending, dict) and pending.get("action") == "confirm_delete":
        pending_intent = sanitize_text(pending.get("intent"))
        pending_item_id = sanitize_text(pending.get("item_id"))
        if is_yes_reply(text):
            deleted = delete_ai_operation(pending_intent, pending_item_id)
            if deleted:
                return {
                    "status": "completed",
                    "reply": "Operazione rimossa correttamente.",
                    "pending": None,
                    "refresh": True,
                }
            return {
                "status": "error",
                "reply": "Non ho trovato l'operazione da eliminare. Potrebbe essere gia stata rimossa.",
                "pending": None,
            }
        if is_no_reply(text):
            return {
                "status": "needs_input",
                "reply": "Ok, eliminazione annullata.",
                "pending": None,
            }
        return {
            "status": "needs_input",
            "reply": "Confermi l'eliminazione? Rispondi con 'si' o 'no'.",
            "pending": pending,
        }

    quick_reply = local_smalltalk_reply(text, pending if isinstance(pending, dict) else None)
    if quick_reply:
        return {
            "status": "needs_input",
            "reply": quick_reply,
            "pending": None if normalize_text(text) in {"annulla", "stop", "cancel", "esci", "lascia perdere"} else (pending if isinstance(pending, dict) else None),
        }

    intent = None
    slots: dict = {}
    if isinstance(pending, dict):
        intent = sanitize_text(pending.get("intent"))
        slots = pending.get("slots") if isinstance(pending.get("slots"), dict) else {}

    local_intent = normalize_ai_intent(intent) or detect_ai_intent(text)
    if local_intent:
        parsed_slots = parse_slots_for_intent(local_intent, text)
        if parsed_slots.get("date_error"):
            return {
                "status": "needs_input",
                "reply": parsed_slots["date_error"],
                "pending": {"intent": local_intent, "slots": {k: v for k, v in slots.items() if k != "date_error"}},
            }
        intent = local_intent
        slots.update({k: v for k, v in parsed_slots.items() if k != "date_error"})

    llm_reply = ""
    if not intent:
        if not get_gemini_api_key():
            return {
                "status": "error",
                "reply": "GEMINI_API_KEY non configurata. Imposta la variabile ambiente (locale o Render) e riavvia l'app.",
                "pending": pending if isinstance(pending, dict) else None,
            }

        llm_result = parse_with_gemini(text, pending)
        if not llm_result.get("ok"):
            return {
                "status": "error",
                "reply": sanitize_text(llm_result.get("reply")) or "Gemini non disponibile in questo momento. Riprova tra poco.",
                "pending": pending if isinstance(pending, dict) else None,
            }

        if llm_result:
            llm_intent = llm_result.get("intent")
            llm_slots = llm_result.get("slots") if isinstance(llm_result.get("slots"), dict) else {}
            llm_reply = sanitize_text(llm_result.get("reply"))
            if llm_intent:
                intent = llm_intent
            slots.update(llm_slots)

    if not intent:
        return {
            "status": "needs_input",
            "reply": llm_reply or "Non ho riconosciuto il comando. Riformulalo in modo piu diretto (azione, importo, soggetto, data).",
            "pending": pending if isinstance(pending, dict) else None,
        }

    if is_delete_intent(intent):
        candidates = find_delete_candidates(intent, slots)
        if not candidates:
            return {
                "status": "needs_input",
                "reply": "Non trovo una voce da eliminare con questi dettagli. Indicami almeno importo e tipo operazione (rimborso, prestito, acquisto casa, ristrutturazione).",
                "pending": {"intent": intent, "slots": slots},
            }

        if len(candidates) > 1:
            preview = "; ".join(describe_item_for_delete(intent, row["item"]) for row in candidates[:3])
            return {
                "status": "needs_input",
                "reply": f"Ho trovato piu voci possibili: {preview}. Aggiungi data o soggetto/voce per identificare quella corretta.",
                "pending": {"intent": intent, "slots": slots},
            }

        target_item = candidates[0]["item"]
        return {
            "status": "needs_input",
            "reply": f"Confermi di voler eliminare {describe_item_for_delete(intent, target_item)}?",
            "pending": {
                "action": "confirm_delete",
                "intent": intent,
                "item_id": target_item.get("id"),
            },
        }

    required = required_slots_for_intent(intent)
    missing = [field for field in required if not slots.get(field)]

    if missing:
        return {
            "status": "needs_input",
            "reply": ask_for_missing_slot(intent, missing[0]),
            "pending": {"intent": intent, "slots": slots},
        }

    item = save_ai_operation(intent, slots)
    return {
        "status": "completed",
        "reply": ai_intent_confirmation(intent, item),
        "pending": None,
        "refresh": True,
    }


def get_db_connection():
    if not USE_DATABASE:
        raise RuntimeError("DATABASE_URL non configurata")
    if psycopg is None or dict_row is None:
        raise RuntimeError("Dipendenza psycopg non installata")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def disable_database_mode(reason: str) -> None:
    """Fall back to local JSON storage when DB is unavailable."""
    global USE_DATABASE, DATABASE_URL
    USE_DATABASE = False
    DATABASE_URL = ""
    print(f"[Home13] Database disabled: {reason}", file=sys.stderr, flush=True)


def ensure_database_ready() -> None:
    if not USE_DATABASE:
        return
    if psycopg is None:
        disable_database_mode(
            "DATABASE_URL presente ma psycopg non disponibile nell'interprete corrente"
        )
        return

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS expenses (
                        id TEXT PRIMARY KEY,
                        category TEXT NOT NULL CHECK (category IN ('acquisto_casa', 'ristrutturazione')),
                        operation_date DATE NOT NULL,
                        description TEXT NOT NULL,
                        amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS loans (
                        id TEXT PRIMARY KEY,
                        operation_date DATE NOT NULL,
                        lender TEXT NOT NULL,
                        amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repayments (
                        id TEXT PRIMARY KEY,
                        operation_date DATE NOT NULL,
                        lender TEXT NOT NULL,
                        amount NUMERIC(14, 2) NOT NULL CHECK (amount > 0)
                    )
                    """
                )
            conn.commit()
    except Exception as exc:
        disable_database_mode(f"inizializzazione DB fallita ({exc.__class__.__name__}: {exc})")

def load_data() -> dict:
    if USE_DATABASE:
        try:
            data = json.loads(json.dumps(DEFAULT_DATA))
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, category, operation_date, description, amount::DOUBLE PRECISION AS amount
                        FROM expenses
                        """
                    )
                    for row in cur.fetchall():
                        data["expenses"][row["category"]].append(
                            {
                                "id": row["id"],
                                "date": row["operation_date"].isoformat(),
                                "description": row["description"],
                                "amount": float(row["amount"]),
                            }
                        )

                    cur.execute(
                        """
                        SELECT id, operation_date, lender, amount::DOUBLE PRECISION AS amount
                        FROM loans
                        """
                    )
                    for row in cur.fetchall():
                        data["loans"].append(
                            {
                                "id": row["id"],
                                "date": row["operation_date"].isoformat(),
                                "lender": row["lender"],
                                "amount": float(row["amount"]),
                            }
                        )

                    cur.execute(
                        """
                        SELECT id, operation_date, lender, amount::DOUBLE PRECISION AS amount
                        FROM repayments
                        """
                    )
                    for row in cur.fetchall():
                        data["repayments"].append(
                            {
                                "id": row["id"],
                                "date": row["operation_date"].isoformat(),
                                "lender": row["lender"],
                                "amount": float(row["amount"]),
                            }
                        )

            return data
        except Exception as exc:
            disable_database_mode(f"lettura DB fallita ({exc.__class__.__name__}: {exc})")

    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA)
        return json.loads(json.dumps(DEFAULT_DATA))

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    data = json.loads(json.dumps(DEFAULT_DATA))
    data["expenses"]["acquisto_casa"] = loaded.get("expenses", {}).get("acquisto_casa", [])
    data["expenses"]["ristrutturazione"] = loaded.get("expenses", {}).get("ristrutturazione", [])
    data["loans"] = loaded.get("loans", [])
    data["repayments"] = loaded.get("repayments", [])
    return data


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sort_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda item: (item.get("date", ""), item.get("id", "")), reverse=True)


def new_id() -> str:
    return uuid4().hex[:10]


def remove_by_id(entries: list[dict], item_id: str) -> bool:
    before = len(entries)
    entries[:] = [item for item in entries if item.get("id") != item_id]
    return len(entries) < before


def update_local_item(
    entries: list[dict],
    item_id: str,
    label_key: str,
    label_value: str,
    amount_value: float,
    date_value: str,
) -> bool:
    for item in entries:
        if item.get("id") == item_id:
            item[label_key] = label_value
            item["amount"] = amount_value
            item["date"] = date_value
            return True
    return False


def sum_amount(entries: list[dict]) -> float:
    return round(sum(float(item.get("amount", 0.0) or 0.0) for item in entries), 2)


def unique_lenders(loans: list[dict], repayments: list[dict]) -> list[str]:
    names = set()
    for item in loans + repayments:
        lender = sanitize_text(item.get("lender", ""))
        if lender:
            names.add(lender)
    return sorted(names, key=str.lower)


def filter_repayments_by_lender(repayments: list[dict], lender: str | None) -> list[dict]:
    selected = sanitize_text(lender)
    if not selected:
        return repayments
    return [row for row in repayments if sanitize_text(row.get("lender", "")) == selected]


def build_chart_data(data: dict) -> dict:
    daily = defaultdict(
        lambda: {
            "acquisto_casa": 0.0,
            "ristrutturazione": 0.0,
            "prestiti": 0.0,
            "rimborsi": 0.0,
        }
    )

    for item in data["expenses"]["acquisto_casa"]:
        daily[item.get("date", "sconosciuto")[:10]]["acquisto_casa"] += float(item.get("amount", 0.0) or 0.0)

    for item in data["expenses"]["ristrutturazione"]:
        daily[item.get("date", "sconosciuto")[:10]]["ristrutturazione"] += float(item.get("amount", 0.0) or 0.0)

    for item in data["loans"]:
        daily[item.get("date", "sconosciuto")[:10]]["prestiti"] += float(item.get("amount", 0.0) or 0.0)

    for item in data["repayments"]:
        daily[item.get("date", "sconosciuto")[:10]]["rimborsi"] += float(item.get("amount", 0.0) or 0.0)

    labels = sorted(daily.keys())
    label_to_idx = {label: idx for idx, label in enumerate(labels)}

    def build_timeline_points(entries: list[dict], label_key: str) -> list[dict]:
        points = []
        for item in entries:
            amount = float(item.get("amount", 0.0) or 0.0)
            if amount <= 0:
                continue

            day_label = (item.get("date", "sconosciuto") or "sconosciuto")[:10]
            if day_label not in label_to_idx:
                continue

            voce = sanitize_text(item.get(label_key, ""))
            points.append(
                {
                    "x": label_to_idx[day_label],
                    "y": round(amount, 2),
                    "voce": voce,
                }
            )

        points.sort(key=lambda row: row["x"])
        return points

    totals = {
        "labels": ["Acquisto casa", "Ristrutturazione", "Prestiti ricevuti", "Rimborsi"],
        "values": [
            round(sum_amount(data["expenses"]["acquisto_casa"]), 2),
            round(sum_amount(data["expenses"]["ristrutturazione"]), 2),
            round(sum_amount(data["loans"]), 2),
            round(sum_amount(data["repayments"]), 2),
        ],
    }

    timeline = {
        "labels": labels,
        "acquistoCasa": build_timeline_points(data["expenses"]["acquisto_casa"], "description"),
        "ristrutturazione": build_timeline_points(data["expenses"]["ristrutturazione"], "description"),
        "prestiti": build_timeline_points(data["loans"], "lender"),
        "rimborsi": build_timeline_points(data["repayments"], "lender"),
    }

    return {
        "totals": totals,
        "timeline": timeline,
    }


def build_lender_balance(loans: list[dict], repayments: list[dict]) -> list[dict]:
    balances = defaultdict(float)

    for entry in loans:
        lender = entry.get("lender", "Sconosciuto")
        balances[lender] += float(entry.get("amount", 0.0) or 0.0)

    for entry in repayments:
        lender = entry.get("lender", "Sconosciuto")
        balances[lender] -= float(entry.get("amount", 0.0) or 0.0)

    result = []
    for lender, amount in sorted(balances.items(), key=lambda item: item[0].lower()):
        result.append({"lender": lender, "balance": round(amount, 2)})
    return result


def build_summary(data: dict) -> dict:
    acquisto_total = sum_amount(data["expenses"]["acquisto_casa"])
    ristr_total = sum_amount(data["expenses"]["ristrutturazione"])
    loans_total = sum_amount(data["loans"])
    repayments_total = sum_amount(data["repayments"])

    return {
        "acquisto_total": acquisto_total,
        "ristr_total": ristr_total,
        "spese_total": round(acquisto_total + ristr_total, 2),
        "loans_total": loans_total,
        "repayments_total": repayments_total,
        "debito_residuo": round(loans_total - repayments_total, 2),
        "cash_netto": round(loans_total - repayments_total - acquisto_total - ristr_total, 2),
        "lender_balance": build_lender_balance(data["loans"], data["repayments"]),
    }


def build_view_model(selected_repayment_lender: str | None = None) -> dict:
    data = load_data()
    data["expenses"]["acquisto_casa"] = sort_entries(data["expenses"]["acquisto_casa"])
    data["expenses"]["ristrutturazione"] = sort_entries(data["expenses"]["ristrutturazione"])
    data["loans"] = sort_entries(data["loans"])
    data["repayments"] = sort_entries(data["repayments"])

    selected_lender = sanitize_text(selected_repayment_lender)
    repayment_lenders = unique_lenders(data["loans"], data["repayments"])
    if selected_lender and selected_lender not in repayment_lenders:
        selected_lender = ""

    filtered_repayments = filter_repayments_by_lender(data["repayments"], selected_lender)

    return {
        "data": data,
        "summary": build_summary(data),
        "chart_data": build_chart_data(data),
        "today": date.today().isoformat(),
        "repayment_lenders": repayment_lenders,
        "selected_repayment_lender": selected_lender,
        "filtered_repayments": filtered_repayments,
    }


_db_bootstrap_done = False


@app.before_request
def bootstrap_database_if_needed():
    global _db_bootstrap_done
    if _db_bootstrap_done:
        return None
    ensure_database_ready()
    _db_bootstrap_done = True
    return None


@app.before_request
def require_login_for_app_routes():
    endpoint = request.endpoint or ""
    public_endpoints = {"login", "health_check", "static", "service_worker"}

    if endpoint in public_endpoints or endpoint.startswith("static"):
        return None
    if is_authenticated():
        return None

    next_path = request.full_path if request.query_string else request.path
    return redirect(url_for("login", next=next_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("index"))

    error = None
    next_path = sanitize_text(request.args.get("next"))

    if request.method == "POST":
        username = sanitize_text(request.form.get("username"))
        password = request.form.get("password") or ""
        form_next = sanitize_text(request.form.get("next"))

        if verify_login_credentials(username, password):
            session.clear()
            session["authenticated"] = True
            session["username"] = AUTH_USERNAME
            target = form_next if _is_safe_next_path(form_next) else next_path
            return redirect(target if _is_safe_next_path(target) else url_for("index"))

        error = "Credenziali non valide."

    return render_template("login.html", error=error, next_path=next_path, default_username=AUTH_USERNAME)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
def index():
    vm = build_view_model(request.args.get("repayment_lender"))
    return render_template(
        "index.html",
        data=vm["data"],
        summary=vm["summary"],
        chart_data=vm["chart_data"],
        today=vm["today"],
        repayment_lenders=vm["repayment_lenders"],
        selected_repayment_lender=vm["selected_repayment_lender"],
        filtered_repayments=vm["filtered_repayments"],
        error=None,
    )


@app.route("/health", methods=["GET"])
def health_check():
    return Response("OK", status=200, mimetype="text/plain")


@app.route("/ai-command", methods=["POST"])
def ai_command():
    payload = request.get_json(silent=True) or {}
    message = sanitize_text(payload.get("message"))
    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else None

    try:
        response_payload = process_ai_command(message, pending)
        return jsonify(response_payload)
    except ValueError as exc:
        return jsonify({"status": "error", "reply": str(exc), "pending": pending}), 400
    except Exception as exc:
        return jsonify({"status": "error", "reply": f"Errore comando AI: {exc}", "pending": pending}), 500


@app.route("/sw.js", methods=["GET"])
def service_worker():
    response = send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/add-expense", methods=["POST"])
def add_expense():
    try:
        category = sanitize_text(request.form.get("category"))
        if category not in {"acquisto_casa", "ristrutturazione"}:
            raise ValueError("Categoria non valida")

        item = {
            "id": new_id(),
            "date": parse_iso_date(request.form.get("date") or "").isoformat(),
            "description": sanitize_text(request.form.get("description")) or "Spesa",
            "amount": parse_amount(request.form.get("amount")),
        }

        if USE_DATABASE:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO expenses (id, category, operation_date, description, amount)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (item["id"], category, item["date"], item["description"], item["amount"]),
                    )
                conn.commit()
        else:
            data = load_data()
            data["expenses"][category].append(item)
            save_data(data)
        return redirect(url_for("index"))
    except ValueError as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=str(exc),
        )


@app.route("/add-loan", methods=["POST"])
def add_loan():
    try:
        item = {
            "id": new_id(),
            "date": parse_iso_date(request.form.get("date") or "").isoformat(),
            "lender": sanitize_text(request.form.get("lender")) or "Familiare",
            "amount": parse_amount(request.form.get("amount")),
        }

        if USE_DATABASE:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO loans (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (item["id"], item["date"], item["lender"], item["amount"]),
                    )
                conn.commit()
        else:
            data = load_data()
            data["loans"].append(item)
            save_data(data)
        return redirect(url_for("index"))
    except ValueError as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=str(exc),
        )


@app.route("/add-repayment", methods=["POST"])
def add_repayment():
    try:
        item = {
            "id": new_id(),
            "date": parse_iso_date(request.form.get("date") or "").isoformat(),
            "lender": sanitize_text(request.form.get("lender")) or "Familiare",
            "amount": parse_amount(request.form.get("amount")),
        }

        if USE_DATABASE:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO repayments (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (item["id"], item["date"], item["lender"], item["amount"]),
                    )
                conn.commit()
        else:
            data = load_data()
            data["repayments"].append(item)
            save_data(data)
        return redirect(url_for("index"))
    except ValueError as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=str(exc),
        )


@app.route("/delete-item", methods=["POST"])
def delete_item():
    section = sanitize_text(request.form.get("section"))
    item_id = sanitize_text(request.form.get("item_id"))
    if not item_id:
        return redirect(url_for("index"))

    if USE_DATABASE:
        deleted = False
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if section in {"acquisto_casa", "ristrutturazione"}:
                    cur.execute("DELETE FROM expenses WHERE id = %s AND category = %s", (item_id, section))
                elif section == "loans":
                    cur.execute("DELETE FROM loans WHERE id = %s", (item_id,))
                elif section == "repayments":
                    cur.execute("DELETE FROM repayments WHERE id = %s", (item_id,))
                deleted = cur.rowcount > 0
            if deleted:
                conn.commit()
    else:
        data = load_data()
        deleted = False

        if section in {"acquisto_casa", "ristrutturazione"}:
            deleted = remove_by_id(data["expenses"][section], item_id)
        elif section == "loans":
            deleted = remove_by_id(data["loans"], item_id)
        elif section == "repayments":
            deleted = remove_by_id(data["repayments"], item_id)

        if deleted:
            save_data(data)
    return redirect(url_for("index"))


@app.route("/edit-item", methods=["POST"])
def edit_item():
    section = sanitize_text(request.form.get("section"))
    item_id = sanitize_text(request.form.get("item_id"))
    label = sanitize_text(request.form.get("label"))
    date_raw = request.form.get("date") or ""

    try:
        amount = parse_amount(request.form.get("amount"))
        operation_date = parse_iso_date(date_raw).isoformat()
    except ValueError as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=str(exc),
        )

    if not item_id or not label:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error="Compila nome e importo per modificare la voce",
        )

    if USE_DATABASE:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if section in {"acquisto_casa", "ristrutturazione"}:
                    cur.execute(
                        """
                        UPDATE expenses
                        SET description = %s, amount = %s, operation_date = %s
                        WHERE id = %s AND category = %s
                        """,
                        (label, amount, operation_date, item_id, section),
                    )
                elif section == "loans":
                    cur.execute(
                        """
                        UPDATE loans
                        SET lender = %s, amount = %s, operation_date = %s
                        WHERE id = %s
                        """,
                        (label, amount, operation_date, item_id),
                    )
                elif section == "repayments":
                    cur.execute(
                        """
                        UPDATE repayments
                        SET lender = %s, amount = %s, operation_date = %s
                        WHERE id = %s
                        """,
                        (label, amount, operation_date, item_id),
                    )
                else:
                    return redirect(url_for("index"))

                if cur.rowcount > 0:
                    conn.commit()
    else:
        data = load_data()
        updated = False
        if section in {"acquisto_casa", "ristrutturazione"}:
            updated = update_local_item(
                data["expenses"][section],
                item_id,
                "description",
                label,
                amount,
                operation_date,
            )
        elif section == "loans":
            updated = update_local_item(data["loans"], item_id, "lender", label, amount, operation_date)
        elif section == "repayments":
            updated = update_local_item(data["repayments"], item_id, "lender", label, amount, operation_date)

        if updated:
            save_data(data)

    return redirect(url_for("index"))


def build_excel_workbook(data: dict):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    summary = build_summary(data)
    amount_format = "#,##0.00"

    def apply_amount_style(sheet, column_index: int, start_row: int = 2) -> None:
        for row_index in range(start_row, sheet.max_row + 1):
            cell = sheet.cell(row=row_index, column=column_index)
            if isinstance(cell.value, (int, float)):
                cell.number_format = amount_format
                cell.alignment = Alignment(horizontal="right")

    wb = Workbook()
    ws = wb.active
    ws.title = "Riepilogo"

    ws.append(["Voce", "Valore"])
    ws.append(["Spese acquisto casa", summary["acquisto_total"]])
    ws.append(["Spese ristrutturazione", summary["ristr_total"]])
    ws.append(["Spese totali", summary["spese_total"]])
    ws.append(["Prestiti ricevuti", summary["loans_total"]])
    ws.append(["Rimborsi effettuati", summary["repayments_total"]])
    ws.append(["Debito residuo prestiti", summary["debito_residuo"]])

    for cell in ws[1]:
        cell.font = Font(bold=True)

    for col in ("A", "B"):
        ws.column_dimensions[col].width = 28
    apply_amount_style(ws, column_index=2)

    ws_lender = wb.create_sheet("Saldo per prestatore")
    ws_lender.append(["Prestatore", "Saldo residuo"])
    for row in summary["lender_balance"]:
        ws_lender.append([row["lender"], row["balance"]])
    for cell in ws_lender[1]:
        cell.font = Font(bold=True)
    ws_lender.column_dimensions["A"].width = 24
    ws_lender.column_dimensions["B"].width = 20
    apply_amount_style(ws_lender, column_index=2)

    def append_sheet(title: str, entries: list[dict], include_lender: bool = False):
        sheet = wb.create_sheet(title)
        if include_lender:
            sheet.append(["Data", "Prestatore", "Importo"])
        else:
            sheet.append(["Data", "Descrizione", "Importo"])

        for item in sort_entries(entries):
            if include_lender:
                sheet.append([item.get("date", ""), item.get("lender", ""), item.get("amount", 0.0)])
            else:
                sheet.append([item.get("date", ""), item.get("description", ""), item.get("amount", 0.0)])

        for cell in sheet[1]:
            cell.font = Font(bold=True)

        sheet.column_dimensions["A"].width = 14
        sheet.column_dimensions["B"].width = 30
        sheet.column_dimensions["C"].width = 14
        apply_amount_style(sheet, column_index=3)

    append_sheet("Acquisto casa", data["expenses"]["acquisto_casa"])
    append_sheet("Ristrutturazione", data["expenses"]["ristrutturazione"])
    append_sheet("Prestiti ricevuti", data["loans"], include_lender=True)
    append_sheet("Rimborsi", data["repayments"], include_lender=True)

    return wb


@app.route("/export/excel", methods=["GET"])
def export_excel():
    try:
        workbook = build_excel_workbook(load_data())
        output = io.BytesIO()
        workbook.save(output)
        output.seek(0)
        filename = f"home13_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return Response(
            output.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=f"Errore export Excel: {exc}",
        )


@app.route("/export/pdf", methods=["GET"])
def export_pdf():
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except Exception as exc:
        vm = build_view_model()
        return render_template(
            "index.html",
            data=vm["data"],
            summary=vm["summary"],
            chart_data=vm["chart_data"],
            today=vm["today"],
            repayment_lenders=vm["repayment_lenders"],
            selected_repayment_lender=vm["selected_repayment_lender"],
            filtered_repayments=vm["filtered_repayments"],
            error=f"Errore export PDF: {exc}",
        )

    data = load_data()
    summary = build_summary(data)

    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            self._saved_page_states.append(dict(self.__dict__))
            page_count = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                draw_page_chrome(self, self._pageNumber, page_count)
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

    buffer = io.BytesIO()
    pdf = NumberedCanvas(buffer, pagesize=A4)
    width, height = A4
    left = 34
    right = width - 34
    content_top = height - 72
    content_bottom = 42

    generated_on = format_date_it(date.today().isoformat())
    palette = {
        "brand": colors.HexColor("#0f4f49"),
        "brand_dark": colors.HexColor("#123f3a"),
        "ink_soft": colors.HexColor("#5d746e"),
        "line": colors.HexColor("#d7e4e0"),
        "line_soft": colors.HexColor("#e3ece9"),
        "header_bg": colors.HexColor("#eef5f3"),
        "row_alt": colors.HexColor("#f9fcfb"),
        "card_bg": colors.HexColor("#f4f9f8"),
    }

    def draw_page_chrome(cnv, page_number: int, page_count: int) -> None:
        cnv.saveState()
        cnv.setStrokeColor(palette["line"])
        cnv.setLineWidth(0.8)
        cnv.line(left, height - 32, right, height - 32)
        cnv.line(left, 24, right, 24)

        cnv.setFillColor(palette["brand"])
        cnv.setFont("Helvetica-Bold", 9)
        cnv.drawString(left, height - 25, "Home13 - Report Spese")

        cnv.setFillColor(palette["ink_soft"])
        cnv.setFont("Helvetica", 8)
        cnv.drawRightString(right, height - 25, f"Generato il {generated_on}")
        cnv.drawString(left, 14, "Documento riservato")
        cnv.drawRightString(right, 14, f"Pag. {page_number}/{page_count}")
        cnv.restoreState()

    def new_page(current_y: float) -> float:
        if current_y < content_bottom:
            pdf.showPage()
            return content_top
        return current_y

    def draw_title(text: str, current_y: float) -> float:
        current_y = new_page(current_y)
        pdf.setFillColor(palette["brand_dark"])
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(left, current_y, text)
        pdf.setStrokeColor(palette["line"])
        pdf.setLineWidth(1)
        pdf.line(left, current_y - 10, right, current_y - 10)
        return current_y - 28

    def draw_section_header(text: str, current_y: float) -> float:
        current_y = new_page(current_y)
        pdf.setFillColor(palette["brand_dark"])
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(left, current_y, text)
        pdf.setStrokeColor(palette["line_soft"])
        pdf.setLineWidth(0.8)
        pdf.line(left, current_y - 3, right, current_y - 3)
        return current_y - 20

    def draw_metric_card(x: float, top_y: float, card_w: float, label: str, value: str) -> None:
        card_h = 56
        pdf.setFillColor(palette["card_bg"])
        pdf.setStrokeColor(palette["line"])
        pdf.roundRect(x, top_y - card_h, card_w, card_h, 8, fill=1, stroke=1)

        pdf.setFillColor(palette["ink_soft"])
        pdf.setFont("Helvetica", 8)
        pdf.drawString(x + 10, top_y - 16, label)

        pdf.setFillColor(palette["brand_dark"])
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(x + 10, top_y - 37, value)

    def draw_summary_cards(current_y: float) -> float:
        card_gap = 10
        card_w = (right - left - card_gap) / 2
        top = current_y

        draw_metric_card(left, top, card_w, "Spese totali", f"EUR {format_euro(summary['spese_total'])}")
        draw_metric_card(left + card_w + card_gap, top, card_w, "Debito residuo", f"EUR {format_euro(summary['debito_residuo'])}")

        top2 = top - 66
        draw_metric_card(left, top2, card_w, "Prestiti ricevuti", f"EUR {format_euro(summary['loans_total'])}")
        draw_metric_card(left + card_w + card_gap, top2, card_w, "Rimborsi effettuati", f"EUR {format_euro(summary['repayments_total'])}")

        return top2 - 74

    def draw_category_bars(current_y: float) -> float:
        current_y = draw_section_header("Sintesi categorie", current_y)
        bar_left = left
        bar_right = right
        bar_width = bar_right - bar_left

        series = [
            ("Acquisto casa", summary["acquisto_total"], colors.HexColor("#4c89e8")),
            ("Ristrutturazione", summary["ristr_total"], colors.HexColor("#ef5c58")),
            ("Prestiti ricevuti", summary["loans_total"], colors.HexColor("#21a77f")),
            ("Rimborsi", summary["repayments_total"], colors.HexColor("#f08c4a")),
        ]
        max_value = max((row[1] for row in series), default=1.0) or 1.0

        y_pos = current_y
        for label, value, color in series:
            y_pos = new_page(y_pos)
            pdf.setFillColor(palette["ink_soft"])
            pdf.setFont("Helvetica", 9)
            pdf.drawString(bar_left, y_pos, label)
            pdf.drawRightString(right, y_pos, f"EUR {format_euro(value)}")

            y_pos -= 8
            pdf.setFillColor(palette["header_bg"])
            pdf.roundRect(bar_left, y_pos - 8, bar_width, 8, 3, fill=1, stroke=0)
            scaled_w = max((value / max_value) * bar_width, 0.0)
            pdf.setFillColor(color)
            pdf.roundRect(bar_left, y_pos - 8, scaled_w, 8, 3, fill=1, stroke=0)
            y_pos -= 18

        return y_pos - 12

    def draw_table(title: str, headers: list[str], rows: list[list[str]], col_widths: list[float], current_y: float) -> float:
        current_y = draw_section_header(title, current_y)
        row_h = 20

        def draw_header_and_frame(y_top: float) -> float:
            pdf.setFillColor(palette["header_bg"])
            pdf.rect(left, y_top - row_h, sum(col_widths), row_h, fill=1, stroke=0)

            x = left
            pdf.setFillColor(palette["brand_dark"])
            pdf.setFont("Helvetica-Bold", 9)
            for idx, header in enumerate(headers):
                pdf.drawString(x + 5, y_top - 12, header)
                x += col_widths[idx]

            pdf.setStrokeColor(palette["line"])
            pdf.setLineWidth(0.6)
            pdf.rect(left, y_top - row_h, sum(col_widths), row_h, fill=0, stroke=1)
            return y_top - row_h

        if not rows:
            pdf.setFillColor(palette["ink_soft"])
            pdf.setFont("Helvetica", 9)
            pdf.drawString(left, current_y, "Nessun dato disponibile")
            return current_y - 20

        current_y = new_page(current_y)
        current_y = draw_header_and_frame(current_y)

        for idx, row in enumerate(rows):
            if current_y - row_h < content_bottom:
                pdf.showPage()
                current_y = content_top
                current_y = draw_header_and_frame(current_y)

            if idx % 2 == 0:
                pdf.setFillColor(palette["row_alt"])
                pdf.rect(left, current_y - row_h, sum(col_widths), row_h, fill=1, stroke=0)

            x = left
            pdf.setFillColor(palette["brand_dark"])
            pdf.setFont("Helvetica", 9)
            for col_idx, cell in enumerate(row):
                text = str(cell)
                if col_idx == len(row) - 1:
                    pdf.drawRightString(x + col_widths[col_idx] - 5, current_y - 12, text)
                else:
                    pdf.drawString(x + 5, current_y - 12, text)
                x += col_widths[col_idx]

            pdf.setStrokeColor(palette["line_soft"])
            pdf.setLineWidth(0.5)
            pdf.line(left, current_y - row_h, left + sum(col_widths), current_y - row_h)
            current_y -= row_h

        return current_y - 18

    y = content_top
    y = draw_title("Report Home13", y)
    y = draw_summary_cards(y)
    y = draw_category_bars(y)

    acquisto_rows = [
        [format_date_it(item.get("date", "")), item.get("description", ""), f"EUR {format_euro(item.get('amount', 0.0))}"]
        for item in sort_entries(data["expenses"]["acquisto_casa"])
    ]
    ristr_rows = [
        [format_date_it(item.get("date", "")), item.get("description", ""), f"EUR {format_euro(item.get('amount', 0.0))}"]
        for item in sort_entries(data["expenses"]["ristrutturazione"])
    ]
    prestiti_rows = [
        [format_date_it(item.get("date", "")), item.get("lender", ""), f"EUR {format_euro(item.get('amount', 0.0))}"]
        for item in sort_entries(data["loans"])
    ]
    rimborsi_rows = [
        [format_date_it(item.get("date", "")), item.get("lender", ""), f"EUR {format_euro(item.get('amount', 0.0))}"]
        for item in sort_entries(data["repayments"])
    ]
    saldo_rows = [[row["lender"], f"EUR {format_euro(row['balance'])}"] for row in summary["lender_balance"]]

    y = draw_table("Acquisto casa", ["Data", "Voce", "Importo"], acquisto_rows, [110, 300, 110], y)
    y = draw_table("Ristrutturazione", ["Data", "Voce", "Importo"], ristr_rows, [110, 300, 110], y)
    y = draw_table("Prestiti ricevuti", ["Data", "Prestatore", "Importo"], prestiti_rows, [110, 300, 110], y)
    y = draw_table("Rimborsi", ["Data", "Prestatore", "Importo"], rimborsi_rows, [110, 300, 110], y)
    draw_table("Saldo per prestatore", ["Prestatore", "Saldo residuo"], saldo_rows, [380, 140], y)

    pdf.save()
    buffer.seek(0)
    filename = f"home13_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    raw_port = (os.environ.get("PORT") or "").strip()
    port = int(raw_port) if raw_port.isdigit() else 5000
    debug_mode = (os.environ.get("FLASK_DEBUG") or "").strip() == "1"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
