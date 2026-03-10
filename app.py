import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from uuid import uuid4

from flask import Flask, Response, redirect, render_template, request, send_from_directory, url_for

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

DEFAULT_DATA = {
    "expenses": {
        "acquisto_casa": [],
        "ristrutturazione": [],
    },
    "loans": [],
    "repayments": [],
}


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


def parse_iso_date(raw_value: str) -> date:
    raw = sanitize_text(raw_value)
    if not raw:
        return date.today()
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError as exc:
        raise ValueError("Data non valida") from exc


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
        "acquistoCasa": [round(daily[label]["acquisto_casa"], 2) for label in labels],
        "ristrutturazione": [round(daily[label]["ristrutturazione"], 2) for label in labels],
        "prestiti": [round(daily[label]["prestiti"], 2) for label in labels],
        "rimborsi": [round(daily[label]["rimborsi"], 2) for label in labels],
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


@app.route("/sw.js", methods=["GET"])
def service_worker():
    response = send_from_directory(os.path.join(BASE_DIR, "static"), "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/manifest.webmanifest", methods=["GET"])
def web_manifest():
    payload = {
        "name": "Home13",
        "short_name": "Home13",
        "description": "Tracciamento spese casa, ristrutturazione, prestiti e rimborsi.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f3f5f4",
        "theme_color": "#0f766e",
        "icons": [
            {
                "src": "/static/favicon.ico",
                "sizes": "any",
                "type": "image/x-icon",
            }
        ],
    }
    response = Response(json.dumps(payload), mimetype="application/manifest+json")
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
    ws.append(["Cash netto", summary["cash_netto"]])

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
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
