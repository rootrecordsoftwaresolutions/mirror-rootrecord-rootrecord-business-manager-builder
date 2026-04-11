"""Clients, invoices, schedule, stock products, supplies — business suite data layer."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from data_api import (
    _business_scope_sql,
    require_writable_business_context,
    resolve_app_timezone,
)
from db import DbConfig, connect, now_utc_iso_text

UTC = timezone.utc


def local_input_to_utc_naive_iso(cfg: DbConfig, text: str) -> str:
    """Parse 'YYYY-MM-DD HH:MM' or date-only as local wall time in app timezone → naive UTC ISO."""
    t = text.strip().replace("T", " ", 1)
    if not t:
        raise ValueError("empty datetime")
    tz = resolve_app_timezone(cfg)
    if len(t) <= 10:
        dt = datetime.strptime(t[:10], "%Y-%m-%d")
        dt = datetime.combine(dt.date(), datetime.min.time())
    else:
        dt = datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
    aware = dt.replace(tzinfo=tz)
    return aware.astimezone(UTC).replace(tzinfo=None).isoformat()


def _utc_naive_to_local_label(cfg: DbConfig, utc_naive_iso: str) -> str:
    if not (utc_naive_iso or "").strip():
        return ""
    dt = datetime.fromisoformat(utc_naive_iso)
    local = dt.replace(tzinfo=UTC).astimezone(resolve_app_timezone(cfg))
    return local.strftime("%Y-%m-%d %H:%M")


# --- Clients ---


def list_clients(cfg: DbConfig, user_id: int, *, include_archived: bool = False) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        q = "SELECT id, display_name, company, email, phone, address, website, tax_id, notes, archived FROM clients WHERE user_id = ?" + extra_biz
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY sort_order, display_name"
        cur = conn.execute(q, (user_id, *extra_biz_params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_client(cfg: DbConfig, user_id: int, client_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, display_name, company, email, phone, address, website, tax_id, notes, archived
            FROM clients WHERE id = ? AND user_id = ?
            """,
            (client_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def save_client(
    cfg: DbConfig,
    user_id: int,
    *,
    client_id: int | None,
    display_name: str,
    company: str = "",
    email: str = "",
    phone: str = "",
    address: str = "",
    website: str = "",
    tax_id: str = "",
    notes: str = "",
) -> int:
    name = (display_name or "").strip()
    if not name:
        raise ValueError("Name required")
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if client_id:
            conn.execute(
                """
                UPDATE clients SET display_name=?, company=?, email=?, phone=?, address=?, website=?,
                  tax_id=?, notes=?, updated_at=?
                WHERE id = ? AND user_id = ?
                """,
                (
                    name,
                    company.strip(),
                    email.strip(),
                    phone.strip(),
                    address.strip(),
                    website.strip(),
                    tax_id.strip(),
                    notes.strip(),
                    now,
                    client_id,
                    user_id,
                ),
            )
            conn.commit()
            return int(client_id)
        cur = conn.execute(
            """
            INSERT INTO clients (user_id, business_id, display_name, company, email, phone, address, website, tax_id, notes, sort_order, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 999, 0, ?, ?)
            """,
            (
                user_id,
                bid,
                name,
                company.strip(),
                email.strip(),
                phone.strip(),
                address.strip(),
                website.strip(),
                tax_id.strip(),
                notes.strip(),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def archive_client(cfg: DbConfig, user_id: int, client_id: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "UPDATE clients SET archived = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (now_utc_iso_text(), client_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


# --- Invoices ---


def next_invoice_number(cfg: DbConfig, user_id: int) -> str:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT invoice_number FROM invoices WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row or not row[0]:
            y = datetime.now().year
            return f"INV-{y}-001"
        last = str(row[0])
        m = re.search(r"(\d+)$", last)
        if m:
            n = int(m.group(1)) + 1
            prefix = last[: m.start()]
            return f"{prefix}{n:03d}"
        return f"{last}-2"
    finally:
        conn.close()


def list_invoices(cfg: DbConfig, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg, "i")
        cur = conn.execute(
            """
            SELECT i.id, i.client_id, i.invoice_number, i.status, i.issued_at_utc, i.due_at_utc, i.currency,
                   i.subtotal_cents, i.tax_cents, i.total_cents, i.notes,
                   c.display_name AS client_name
            FROM invoices i
            LEFT JOIN clients c ON c.id = i.client_id
            WHERE i.user_id = ? {extra_biz}
            ORDER BY i.issued_at_utc DESC, i.id DESC
            LIMIT ?
            """.format(extra_biz=extra_biz),
            (user_id, *extra_biz_params, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_invoice(cfg: DbConfig, user_id: int, invoice_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT i.id, i.client_id, i.invoice_number, i.status, i.issued_at_utc, i.due_at_utc, i.currency,
                   i.subtotal_cents, i.tax_cents, i.total_cents, i.notes,
                   c.display_name AS client_name, c.company AS client_company, c.email AS client_email,
                   c.address AS client_address
            FROM invoices i
            LEFT JOIN clients c ON c.id = i.client_id
            WHERE i.id = ? AND i.user_id = ?
            """,
            (invoice_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def list_invoice_lines(cfg: DbConfig, user_id: int, invoice_id: int) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        cur = conn.execute(
            """
            SELECT id, sort_order, description, quantity, unit_price_cents, line_total_cents
            FROM invoice_lines WHERE invoice_id = ? ORDER BY sort_order, id
            """,
            (invoice_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _ensure_invoice_owner(conn: sqlite3.Connection, user_id: int, invoice_id: int) -> None:
    r = conn.execute(
        "SELECT 1 FROM invoices WHERE id = ? AND user_id = ?",
        (invoice_id, user_id),
    ).fetchone()
    if not r:
        raise ValueError("invoice not found")


def recalculate_invoice_totals(cfg: DbConfig, user_id: int, invoice_id: int) -> None:
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        row = conn.execute(
            "SELECT tax_cents FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        tax_cents = int(row[0] or 0)
        s = conn.execute(
            "SELECT COALESCE(SUM(line_total_cents), 0) FROM invoice_lines WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()[0]
        sub = int(s or 0)
        total = sub + tax_cents
        conn.execute(
            """
            UPDATE invoices SET subtotal_cents = ?, total_cents = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (sub, total, now_utc_iso_text(), invoice_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def create_invoice(
    cfg: DbConfig,
    user_id: int,
    *,
    client_id: int | None,
    invoice_number: str | None,
    issued_at_utc: str,
    due_at_utc: str | None,
    currency: str = "USD",
    tax_cents: int = 0,
    notes: str = "",
    status: str = "draft",
) -> int:
    num = (invoice_number or "").strip() or next_invoice_number(cfg, user_id)
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO invoices (user_id, business_id, client_id, invoice_number, status, issued_at_utc, due_at_utc,
              currency, subtotal_cents, tax_cents, total_cents, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?)
            """,
            (
                user_id,
                bid,
                client_id,
                num,
                status,
                issued_at_utc,
                due_at_utc,
                currency,
                tax_cents,
                notes.strip(),
                now,
                now,
            ),
        )
        iid = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO invoice_lines (invoice_id, sort_order, description, quantity, unit_price_cents, line_total_cents)
            VALUES (?, 0, 'Service', 1, 0, 0)
            """,
            (iid,),
        )
        conn.commit()
        recalculate_invoice_totals(cfg, user_id, iid)
        return iid
    except sqlite3.IntegrityError as e:
        conn.rollback()
        raise ValueError(f"invoice number in use: {e}") from e
    finally:
        conn.close()


def update_invoice_meta(
    cfg: DbConfig,
    user_id: int,
    invoice_id: int,
    *,
    client_id: int | None = None,
    invoice_number: str | None = None,
    status: str | None = None,
    issued_at_utc: str | None = None,
    due_at_utc: str | None = None,
    tax_cents: int | None = None,
    notes: str | None = None,
) -> None:
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        parts: list[str] = []
        vals: list[Any] = []
        if client_id is not None:
            parts.append("client_id = ?")
            vals.append(client_id)
        if invoice_number is not None and str(invoice_number).strip():
            parts.append("invoice_number = ?")
            vals.append(str(invoice_number).strip())
        if status is not None:
            parts.append("status = ?")
            vals.append(status)
        if issued_at_utc is not None:
            parts.append("issued_at_utc = ?")
            vals.append(issued_at_utc)
        if due_at_utc is not None:
            parts.append("due_at_utc = ?")
            vals.append(due_at_utc)
        if tax_cents is not None:
            parts.append("tax_cents = ?")
            vals.append(tax_cents)
        if notes is not None:
            parts.append("notes = ?")
            vals.append(notes)
        if not parts:
            return
        parts.append("updated_at = ?")
        vals.append(now_utc_iso_text())
        vals.extend([invoice_id, user_id])
        conn.execute(
            f"UPDATE invoices SET {', '.join(parts)} WHERE id = ? AND user_id = ?",
            vals,
        )
        conn.commit()
        recalculate_invoice_totals(cfg, user_id, invoice_id)
    finally:
        conn.close()


def save_invoice_line(
    cfg: DbConfig,
    user_id: int,
    invoice_id: int,
    *,
    line_id: int | None,
    description: str,
    quantity: float,
    unit_price_cents: int,
) -> int:
    qty = float(quantity) if quantity else 1.0
    unit = int(unit_price_cents)
    line_total = int(round(qty * unit))
    desc = (description or "").strip() or "Item"
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        if line_id:
            conn.execute(
                """
                UPDATE invoice_lines SET description=?, quantity=?, unit_price_cents=?, line_total_cents=?
                WHERE id = ? AND invoice_id = ?
                """,
                (desc, qty, unit, line_total, line_id, invoice_id),
            )
            conn.commit()
            recalculate_invoice_totals(cfg, user_id, invoice_id)
            return int(line_id)
        cur = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM invoice_lines WHERE invoice_id = ?",
            (invoice_id,),
        )
        so = int(cur.fetchone()[0])
        cur = conn.execute(
            """
            INSERT INTO invoice_lines (invoice_id, sort_order, description, quantity, unit_price_cents, line_total_cents)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, so, desc, qty, unit, line_total),
        )
        conn.commit()
        recalculate_invoice_totals(cfg, user_id, invoice_id)
        return int(cur.lastrowid)
    finally:
        conn.close()


def delete_invoice_line(cfg: DbConfig, user_id: int, invoice_id: int, line_id: int) -> None:
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        conn.execute(
            "DELETE FROM invoice_lines WHERE id = ? AND invoice_id = ?",
            (line_id, invoice_id),
        )
        conn.commit()
        recalculate_invoice_totals(cfg, user_id, invoice_id)
    finally:
        conn.close()


def delete_invoice(cfg: DbConfig, user_id: int, invoice_id: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute("DELETE FROM invoices WHERE id = ? AND user_id = ?", (invoice_id, user_id))
        conn.commit()
    finally:
        conn.close()


def parse_invoice_lines_text(raw: str) -> list[tuple[str, float, int]]:
    """Each non-empty line: description | qty | unit price (major units e.g. 150.00)."""
    out: list[tuple[str, float, int]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if not parts[0]:
            continue
        desc = parts[0]
        try:
            qty = float(parts[1]) if len(parts) > 1 else 1.0
        except ValueError:
            qty = 1.0
        try:
            major = float((parts[2] if len(parts) > 2 else "0").replace(",", "."))
        except ValueError:
            major = 0.0
        out.append((desc, qty, int(round(major * 100))))
    return out


def replace_invoice_lines(
    cfg: DbConfig,
    user_id: int,
    invoice_id: int,
    lines: list[tuple[str, float, int]],
) -> None:
    conn = connect(cfg)
    try:
        _ensure_invoice_owner(conn, user_id, invoice_id)
        conn.execute("DELETE FROM invoice_lines WHERE invoice_id = ?", (invoice_id,))
        use = lines if lines else [("Line item", 1.0, 0)]
        for i, (desc, qty, unit) in enumerate(use):
            qty = float(qty) if qty else 1.0
            unit = int(unit)
            lt = int(round(qty * unit))
            conn.execute(
                """
                INSERT INTO invoice_lines (invoice_id, sort_order, description, quantity, unit_price_cents, line_total_cents)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (invoice_id, i, (desc or "Item").strip(), qty, unit, lt),
            )
        conn.commit()
    finally:
        conn.close()
    recalculate_invoice_totals(cfg, user_id, invoice_id)


def write_invoice_pdf(cfg: DbConfig, user_id: int, invoice_id: int, path: str) -> None:
    from data_api import settings_get

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError as e:
        raise RuntimeError("PDF export requires reportlab") from e
    inv = get_invoice(cfg, user_id, invoice_id)
    if not inv:
        raise ValueError("invoice not found")
    lines = list_invoice_lines(cfg, user_id, invoice_id)
    c = canvas.Canvas(path, pagesize=letter)
    w, h = letter
    y = h - 48
    left = 48

    def ln(txt: str, step: int = 14, *, bold: bool = False, size: int = 10) -> None:
        nonlocal y
        if y < 56:
            c.showPage()
            y = h - 48
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(left, y, (txt or "")[:100])
        y -= step

    ln("INVOICE", 24, bold=True, size=16)
    y -= 4
    ln(f"No. {inv['invoice_number']}", bold=True, size=11)
    ln(f"Status: {inv['status']}")
    ln(
        f"Issued (UTC): {str(inv.get('issued_at_utc', ''))[:19]}  |  Due (UTC): {str(inv.get('due_at_utc') or '—')[:19]}"
    )
    y -= 6
    ln("From", bold=True, size=11)
    ln(str(settings_get(cfg, "business_name", "") or "—"))
    ln(str(settings_get(cfg, "business_address", "") or ""))
    y -= 6
    ln("Bill to", bold=True, size=11)
    ln(str(inv.get("client_name") or "—"))
    if inv.get("client_company"):
        ln(str(inv["client_company"]))
    if inv.get("client_email"):
        ln(str(inv["client_email"]))
    if inv.get("client_address"):
        ln(str(inv["client_address"]))
    y -= 8
    ln("Line items", bold=True, size=11)
    for row in lines:
        d = str(row.get("description", ""))[:60]
        q = row.get("quantity", 1)
        unit = int(row.get("unit_price_cents") or 0)
        lt = int(row.get("line_total_cents") or 0)
        ln(f"  • {d}  ×{q} @ {unit / 100:,.2f} = {lt / 100:,.2f} {inv.get('currency', 'USD')}")
    y -= 6
    cur = str(inv.get("currency", "USD"))
    ln(f"Subtotal: {cur} {int(inv.get('subtotal_cents') or 0) / 100:,.2f}")
    ln(f"Tax: {cur} {int(inv.get('tax_cents') or 0) / 100:,.2f}")
    ln(f"Total: {cur} {int(inv.get('total_cents') or 0) / 100:,.2f}", bold=True, size=12)
    if inv.get("notes"):
        y -= 8
        ln("Notes", bold=True)
        for part in str(inv["notes"]).splitlines()[:12]:
            ln(part[:96])
    c.save()


# --- Schedule ---


def list_schedule_events(
    cfg: DbConfig,
    user_id: int,
    *,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg, "e")
        cur = conn.execute(
            """
            SELECT e.id, e.title, e.starts_at_utc, e.ends_at_utc, e.all_day, e.client_id, e.project_id,
                   e.location, e.notes, e.status,
                   c.display_name AS client_name, p.name AS project_name
            FROM schedule_events e
            LEFT JOIN clients c ON c.id = e.client_id
            LEFT JOIN projects p ON p.id = e.project_id
            WHERE e.user_id = ? AND e.starts_at_utc >= ? AND e.starts_at_utc < ? {extra_biz}
            ORDER BY e.starts_at_utc ASC
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def list_schedule_events_upcoming(cfg: DbConfig, user_id: int, days: int = 21) -> list[dict[str, Any]]:
    now_s = now_utc_iso_text()
    now = datetime.fromisoformat(now_s)
    end = (now + timedelta(days=days)).isoformat()
    return list_schedule_events(cfg, user_id, start_utc=now_s, end_utc=end)


def save_schedule_event(
    cfg: DbConfig,
    user_id: int,
    *,
    event_id: int | None,
    title: str,
    starts_at_utc: str,
    ends_at_utc: str | None,
    all_day: int = 0,
    client_id: int | None = None,
    project_id: int | None = None,
    location: str = "",
    notes: str = "",
    status: str = "scheduled",
) -> int:
    tit = (title or "").strip()
    if not tit:
        raise ValueError("Title required")
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if event_id:
            conn.execute(
                """
                UPDATE schedule_events SET title=?, starts_at_utc=?, ends_at_utc=?, all_day=?, client_id=?,
                  project_id=?, location=?, notes=?, status=?, updated_at=?
                WHERE id = ? AND user_id = ?
                """,
                (
                    tit,
                    starts_at_utc,
                    ends_at_utc,
                    all_day,
                    client_id,
                    project_id,
                    location.strip(),
                    notes.strip(),
                    status,
                    now,
                    event_id,
                    user_id,
                ),
            )
            conn.commit()
            return int(event_id)
        cur = conn.execute(
            """
            INSERT INTO schedule_events (user_id, business_id, title, starts_at_utc, ends_at_utc, all_day, client_id, project_id,
              location, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                bid,
                tit,
                starts_at_utc,
                ends_at_utc,
                all_day,
                client_id,
                project_id,
                location.strip(),
                notes.strip(),
                status,
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def delete_schedule_event(cfg: DbConfig, user_id: int, event_id: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute("DELETE FROM schedule_events WHERE id = ? AND user_id = ?", (event_id, user_id))
        conn.commit()
    finally:
        conn.close()


def get_schedule_event(cfg: DbConfig, user_id: int, event_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, title, starts_at_utc, ends_at_utc, all_day, client_id, project_id, location, notes, status
            FROM schedule_events WHERE id = ? AND user_id = ?
            """,
            (event_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def schedule_local_labels(cfg: DbConfig, row: dict[str, Any]) -> dict[str, str]:
    return {
        "start_local": _utc_naive_to_local_label(cfg, str(row.get("starts_at_utc", ""))),
        "end_local": _utc_naive_to_local_label(cfg, str(row.get("ends_at_utc") or "")),
    }


# --- Stock / products (sellable inventory) ---


def list_stock_products(cfg: DbConfig, user_id: int, *, include_archived: bool = False) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        q = (
            "SELECT id, name, sku, unit, qty_on_hand, reorder_level, unit_cost_cents, unit_price_cents, "
            "currency, notes, archived FROM stock_products WHERE user_id = ?" + extra_biz
        )
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY sort_order, name"
        cur = conn.execute(q, (user_id, *extra_biz_params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_stock_product(cfg: DbConfig, user_id: int, product_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, name, sku, description, unit, qty_on_hand, reorder_level, unit_cost_cents, unit_price_cents,
                   currency, notes, archived
            FROM stock_products WHERE id = ? AND user_id = ?
            """,
            (product_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def save_stock_product(
    cfg: DbConfig,
    user_id: int,
    *,
    product_id: int | None,
    name: str,
    sku: str = "",
    description: str = "",
    unit: str = "ea",
    qty_on_hand: float = 0.0,
    reorder_level: float = 0.0,
    unit_cost_cents: int | None = None,
    unit_price_cents: int | None = None,
    currency: str = "USD",
    notes: str = "",
) -> int:
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Product name required")
    now = now_utc_iso_text()
    u = (unit or "ea").strip() or "ea"
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if product_id:
            conn.execute(
                """
                UPDATE stock_products SET name=?, sku=?, description=?, unit=?, qty_on_hand=?, reorder_level=?,
                  unit_cost_cents=?, unit_price_cents=?, currency=?, notes=?, updated_at=?
                WHERE id = ? AND user_id = ?
                """,
                (
                    nm,
                    sku.strip(),
                    description.strip(),
                    u,
                    float(qty_on_hand),
                    float(reorder_level),
                    unit_cost_cents,
                    unit_price_cents,
                    (currency or "USD").strip(),
                    notes.strip(),
                    now,
                    product_id,
                    user_id,
                ),
            )
            conn.commit()
            return int(product_id)
        cur = conn.execute(
            """
            INSERT INTO stock_products (user_id, business_id, name, sku, description, unit, qty_on_hand, reorder_level,
              unit_cost_cents, unit_price_cents, currency, notes, sort_order, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 999, 0, ?, ?)
            """,
            (
                user_id,
                bid,
                nm,
                sku.strip(),
                description.strip(),
                u,
                float(qty_on_hand),
                float(reorder_level),
                unit_cost_cents,
                unit_price_cents,
                (currency or "USD").strip(),
                notes.strip(),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def archive_stock_product(cfg: DbConfig, user_id: int, product_id: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "UPDATE stock_products SET archived = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (now_utc_iso_text(), product_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def adjust_stock_product_qty(cfg: DbConfig, user_id: int, product_id: int, delta: float) -> float:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT qty_on_hand FROM stock_products WHERE id = ? AND user_id = ?",
            (product_id, user_id),
        ).fetchone()
        if not row:
            raise ValueError("product not found")
        new_q = float(row[0] or 0) + float(delta)
        conn.execute(
            "UPDATE stock_products SET qty_on_hand = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (new_q, now_utc_iso_text(), product_id, user_id),
        )
        conn.commit()
        return new_q
    finally:
        conn.close()


# --- Supplies (internal / consumables) ---


def list_supplies(cfg: DbConfig, user_id: int, *, include_archived: bool = False) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        q = (
            "SELECT id, name, category, unit, qty_on_hand, reorder_level, vendor, notes, archived "
            "FROM supplies WHERE user_id = ?" + extra_biz
        )
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY sort_order, name"
        cur = conn.execute(q, (user_id, *extra_biz_params))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def get_supply(cfg: DbConfig, user_id: int, supply_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, name, category, unit, qty_on_hand, reorder_level, vendor, notes, archived
            FROM supplies WHERE id = ? AND user_id = ?
            """,
            (supply_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def save_supply(
    cfg: DbConfig,
    user_id: int,
    *,
    supply_id: int | None,
    name: str,
    category: str = "",
    unit: str = "ea",
    qty_on_hand: float = 0.0,
    reorder_level: float = 0.0,
    vendor: str = "",
    notes: str = "",
) -> int:
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Supply name required")
    now = now_utc_iso_text()
    u = (unit or "ea").strip() or "ea"
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if supply_id:
            conn.execute(
                """
                UPDATE supplies SET name=?, category=?, unit=?, qty_on_hand=?, reorder_level=?,
                  vendor=?, notes=?, updated_at=?
                WHERE id = ? AND user_id = ?
                """,
                (
                    nm,
                    category.strip(),
                    u,
                    float(qty_on_hand),
                    float(reorder_level),
                    vendor.strip(),
                    notes.strip(),
                    now,
                    supply_id,
                    user_id,
                ),
            )
            conn.commit()
            return int(supply_id)
        cur = conn.execute(
            """
            INSERT INTO supplies (user_id, business_id, name, category, unit, qty_on_hand, reorder_level, vendor, notes,
              sort_order, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 999, 0, ?, ?)
            """,
            (
                user_id,
                bid,
                nm,
                category.strip(),
                u,
                float(qty_on_hand),
                float(reorder_level),
                vendor.strip(),
                notes.strip(),
                now,
                now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def archive_supply(cfg: DbConfig, user_id: int, supply_id: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "UPDATE supplies SET archived = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (now_utc_iso_text(), supply_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def adjust_supply_qty(cfg: DbConfig, user_id: int, supply_id: int, delta: float) -> float:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT qty_on_hand FROM supplies WHERE id = ? AND user_id = ?",
            (supply_id, user_id),
        ).fetchone()
        if not row:
            raise ValueError("supply not found")
        new_q = float(row[0] or 0) + float(delta)
        conn.execute(
            "UPDATE supplies SET qty_on_hand = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (new_q, now_utc_iso_text(), supply_id, user_id),
        )
        conn.commit()
        return new_q
    finally:
        conn.close()
