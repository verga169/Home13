import io
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime
from uuid import uuid4

from flask import Flask, Response, redirect, render_template, request, url_for

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:
    psycopg = None
    dict_row = None

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data_store.json")
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


def _bootstrap_json_to_db_if_empty() -> None:
    if not os.path.exists(DATA_FILE):
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        local_data = json.load(f)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM expenses")
            expenses_count = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM loans")
            loans_count = int(cur.fetchone()["count"])
            cur.execute("SELECT COUNT(*) AS count FROM repayments")
            repayments_count = int(cur.fetchone()["count"])

            if expenses_count == 0 and loans_count == 0 and repayments_count == 0:
                for item in local_data.get("expenses", {}).get("acquisto_casa", []):
                    cur.execute(
                        """
                        INSERT INTO expenses (id, category, operation_date, description, amount)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            item.get("id") or new_id(),
                            "acquisto_casa",
                            parse_iso_date(item.get("date", "")).isoformat(),
                            sanitize_text(item.get("description", "")) or "Spesa",
                            float(item.get("amount", 0.0) or 0.0),
                        ),
                    )

                for item in local_data.get("expenses", {}).get("ristrutturazione", []):
                    cur.execute(
                        """
                        INSERT INTO expenses (id, category, operation_date, description, amount)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            item.get("id") or new_id(),
                            "ristrutturazione",
                            parse_iso_date(item.get("date", "")).isoformat(),
                            sanitize_text(item.get("description", "")) or "Spesa",
                            float(item.get("amount", 0.0) or 0.0),
                        ),
                    )

                for item in local_data.get("loans", []):
                    cur.execute(
                        """
                        INSERT INTO loans (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            item.get("id") or new_id(),
                            parse_iso_date(item.get("date", "")).isoformat(),
                            sanitize_text(item.get("lender", "")) or "Familiare",
                            float(item.get("amount", 0.0) or 0.0),
                        ),
                    )

                for item in local_data.get("repayments", []):
                    cur.execute(
                        """
                        INSERT INTO repayments (id, operation_date, lender, amount)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            item.get("id") or new_id(),
                            parse_iso_date(item.get("date", "")).isoformat(),
                            sanitize_text(item.get("lender", "")) or "Familiare",
                            float(item.get("amount", 0.0) or 0.0),
                        ),
                    )

        conn.commit()


def ensure_database_ready() -> None:
    if not USE_DATABASE:
        return
    if psycopg is None:
        raise RuntimeError("DATABASE_URL configurata ma psycopg non e installata")

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

    _bootstrap_json_to_db_if_empty()


def load_data() -> dict:
    if USE_DATABASE:
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


def build_excel_workbook(data: dict):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    summary = build_summary(data)

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

    ws_lender = wb.create_sheet("Saldo per prestatore")
    ws_lender.append(["Prestatore", "Saldo residuo"])
    for row in summary["lender_balance"]:
        ws_lender.append([row["lender"], row["balance"]])
    for cell in ws_lender[1]:
        cell.font = Font(bold=True)
    ws_lender.column_dimensions["A"].width = 24
    ws_lender.column_dimensions["B"].width = 20

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

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    def new_page(current_y: float) -> float:
        if current_y < 70:
            pdf.showPage()
            return height - 48
        return current_y

    def draw_title(text: str, current_y: float) -> float:
        current_y = new_page(current_y)
        pdf.setFont("Helvetica-Bold", 13)
        pdf.drawString(40, current_y, text)
        return current_y - 18

    def draw_row(left_text: str, right_text: str, current_y: float) -> float:
        current_y = new_page(current_y)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(44, current_y, left_text)
        pdf.drawRightString(width - 44, current_y, right_text)
        return current_y - 14

    def draw_entries(title: str, entries: list[dict], include_lender: bool, current_y: float) -> float:
        current_y = draw_title(title, current_y)
        if not entries:
            return draw_row("Nessuna voce", "", current_y - 2)

        for item in sort_entries(entries):
            if include_lender:
                left = f"{item.get('date', '')} | {item.get('lender', '')}"
            else:
                left = f"{item.get('date', '')} | {item.get('description', '')}"
            right = f"EUR {format_euro(item.get('amount', 0.0))}"
            current_y = draw_row(left, right, current_y)
        return current_y - 6

    y = height - 44
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(40, y, "Report Home13")
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(width - 44, y + 1, f"Generato il {date.today().isoformat()}")
    y -= 26

    y = draw_title("Riepilogo", y)
    y = draw_row("Spese acquisto casa", f"EUR {format_euro(summary['acquisto_total'])}", y)
    y = draw_row("Spese ristrutturazione", f"EUR {format_euro(summary['ristr_total'])}", y)
    y = draw_row("Spese totali", f"EUR {format_euro(summary['spese_total'])}", y)
    y = draw_row("Prestiti ricevuti", f"EUR {format_euro(summary['loans_total'])}", y)
    y = draw_row("Rimborsi effettuati", f"EUR {format_euro(summary['repayments_total'])}", y)
    y = draw_row("Debito residuo", f"EUR {format_euro(summary['debito_residuo'])}", y)
    y = draw_row("Cash netto", f"EUR {format_euro(summary['cash_netto'])}", y - 4)
    y -= 10

    y = draw_entries("Acquisto casa", data["expenses"]["acquisto_casa"], include_lender=False, current_y=y)
    y = draw_entries("Ristrutturazione", data["expenses"]["ristrutturazione"], include_lender=False, current_y=y)
    y = draw_entries("Prestiti ricevuti", data["loans"], include_lender=True, current_y=y)
    y = draw_entries("Rimborsi", data["repayments"], include_lender=True, current_y=y)

    y = draw_title("Saldo per prestatore", y)
    if summary["lender_balance"]:
        for row in summary["lender_balance"]:
            y = draw_row(row["lender"], f"EUR {format_euro(row['balance'])}", y)
    else:
        y = draw_row("Nessun saldo", "", y)

    pdf.save()
    buffer.seek(0)
    filename = f"home13_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


ensure_database_ready()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
