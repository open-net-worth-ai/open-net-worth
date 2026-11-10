"""The Singapore chapter's Documents — eight synthetic files, rendered on the day of the build.

The files are GENERATED rather than checked in as binaries, for the reason the whole book is:
each one quotes a date and a figure that must agree with the ledger it is evidence for, and a
committed PDF would keep saying "payment received 2026-09-20" long after the nightly rebuild
moved that payment. Every line below is fiction; every document says so.

They go in through ``vault_changes.add_file`` (the same parser and Vault metadata a hand
upload gets). One of them is stored Private on purpose, so a demo visitor can watch Lucius
refuse it; the Kopi Lane receipt is additionally promoted to a real receipt and linked to the
transaction it evidences, through ``receipts.apply_receipt_change``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

FOOTER = "SYNTHETIC DEMO - no real customer data"
NOTE = "Synthetic demo original; safe to download and reuse for rehearsal."
# Planted in the Private memo so a chat can prove Lucius never read it: it must never surface.
PRIVACY_MARKER = "CORAL-PAPER-481"


@dataclass(frozen=True)
class Facts:
    """What the documents quote, taken from the book that was just written."""

    today: date
    paid: date  # the consulting invoice's payment date
    coffee: float  # the receipted Kopi Lane charge
    rent: float
    goals: tuple[tuple[str, float, float], ...]  # (name, target, earmarked) in SGD
    sg_etf: dict  # {"cost": float, "price": float, "qty": float, "fees": float}
    eu_etf: dict


def _money(value: float) -> str:
    return f"{value:,.2f}" if value % 1 else f"{value:,.0f}"


def _household_plan(f: Facts) -> list[str]:
    (deposit, d_target, d_held), (sabbatical, s_target, s_held) = f.goals
    return [
        "Household financial plan",
        f"Prepared: {f.today.isoformat()}",
        "Alex and Sam Morgan / Singapore residence / global assets",
        "Singapore Everyday: operating cash. Singapore Runway: protected reserves.",
        "USD is the reporting currency. Native SGD/EUR amounts stay on their own legs.",
        f"{deposit}: SGD {_money(d_target)} target, SGD {_money(d_held)} earmarked.",
        f"{sabbatical}: SGD {_money(s_target)} target, SGD {_money(s_held)} earmarked.",
        "Retirement target: age 52. Compare the three saved Outlook scenarios.",
        "Goals earmark existing cash; never add them to net worth again.",
        "Global ETFs, employer equity and crypto: review concentration before adding risk.",
        "This is a fictional international household with legacy US assets.",
    ]


def _consulting_invoice(f: Facts) -> list[str]:
    return [
        "Consulting invoice",
        f"Prepared: {f.today.isoformat()}",
        f"Atlas Studio / Invoice DEMO-{f.paid.year}-014 / Service: product systems review",
        "Bill to: Atlas Studio (fictional) / Consultant: Alex Morgan (fictional)",
        "Amount: EUR 2,400 / Paid to: Consulting EUR",
        f"Payment received: {f.paid.isoformat()}",
        "The matching ledger income is already booked. This document is evidence.",
    ]


def _asia_brokerage(f: Facts) -> list[str]:
    sg, eu = f.sg_etf, f.eu_etf
    return [
        "Asia brokerage trade note",
        f"Prepared: {f.today.isoformat()}",
        "Account: Asia Brokerage SGD / All figures are synthetic",
        f"Acquired: {_money(sg['qty'])} ES3.SI units at SGD {sg['cost']:.3f} / "
        f"Gross SGD {_money(sg['qty'] * sg['cost'])} / Fee SGD {_money(sg['fees'])}",
        "The lot and settlement debit were booked together through the acquisition service.",
        f"Demo valuation: SGD {sg['price']:.3f} per unit. This is not a live market quote.",
        f"Global Brokerage EUR: {_money(eu['qty'])} EUNL.DE units at EUR {eu['cost']:.2f} / "
        f"Fee EUR {_money(eu['fees'])}.",
        f"Demo valuation: EUR {eu['price']:.2f} per unit. Keep currencies on their own legs.",
    ]


def _home_lease(f: Facts) -> list[str]:
    return [
        "Singapore home lease",
        f"Prepared: {f.today.isoformat()}",
        "Harbour Residences (fictional) / Tenant: Alex and Sam Morgan (fictional)",
        f"Monthly rent: SGD {_money(f.rent)} / Paid from: Singapore Everyday",
        "Purpose: a recurring housing obligation, already represented in the ledger.",
        "No actual address, identification number or signed personal agreement is included.",
    ]


def _private_memo(f: Facts) -> list[str]:
    return [
        "Private tax planning memo",
        f"Prepared: {f.today.isoformat()}",
        "Fictional household working note. Stored Private in Documents.",
        f"Demo privacy marker: {PRIVACY_MARKER}. Lucius must not read these contents.",
        "Cross-border residency questions belong with a qualified adviser.",
        "No real tax return, identification number, employer record or signature is copied.",
    ]


def _receipt(f: Facts) -> list[str]:
    return [
        "Kopi Lane receipt",
        f"Prepared: {f.today.isoformat()}",
        f"Transaction date: {f.today.isoformat()} / Merchant: Kopi Lane (fictional)",
        f"2 iced coffees and toast / Total paid: SGD {f.coffee:.2f}",
        f"Paid from: Singapore Everyday / Receipt DEMO-KL-{round(f.coffee * 100)}",
        "OFFLINE EMAIL REHEARSAL: evidence linked to an explicitly seeded transaction.",
        "No email connector or AI automation run is claimed to have produced this receipt.",
    ]


def _import_csv(f: Facts) -> str:
    day = f.today.isoformat()
    return (
        "Date,Description,Amount,Currency,Account\n"
        f"{day},Harbour Electronics,-249.00,SGD,Singapore Everyday\n"
        f"{day},Neighborhood Mart,-64.80,SGD,Singapore Everyday\n"
        f"{day},MetroRide,-16.40,SGD,Singapore Everyday\n"
    )


def _bank_notification(f: Facts) -> str:
    return (
        f"{FOOTER}\n"
        "OFFLINE EMAIL REHEARSAL - not a live inbox fetch\n"
        "From: notifications@harbour-bank.example\n"
        "To: alex.morgan@example.test\n"
        "Message-ID: <demo-kopi-2790@harbour-bank.example>\n"
        f"Date: {f.today.isoformat()}\n"
        "Subject: Debit card payment SGD 27.90\n"
        "\n"
        "Account: Singapore Everyday\n"
        "Merchant: Kopi Lane\n"
        "Amount: SGD 27.90\n"
        "Reference: DEMO-KOPI-2790\n"
        f"This is a NEW rehearsal item; do not confuse it with the already-booked "
        f"SGD {f.coffee:.2f} receipt. Resolve account/category, check duplicates, then ask "
        "for approval before a ledger write.\n"
    )


def _pdf(lines: list[str]) -> bytes:
    """A one-page A4 PDF: brand line, bold title, body, footer. Plain Helvetica."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((56, 64), "OPEN NET WORTH / DEMO HOUSEHOLD", fontname="helv", fontsize=9)
    page.insert_text((56, 104), lines[0], fontname="hebo", fontsize=20)
    y = 140
    for line in lines[1:]:
        page.insert_text((56, y), line, fontname="helv", fontsize=10.5)
        y += 20
    page.insert_text((56, 790), FOOTER, fontname="helv", fontsize=8)
    return doc.tobytes(deflate=True, no_new_id=True)


@dataclass(frozen=True)
class Doc:
    filename: str
    mime: str
    title: str
    content: bytes
    private: bool = False


def documents(f: Facts) -> list[Doc]:
    """Every file, in the order they appear in the Documents screen's oldest-first listing."""
    pdf = "application/pdf"
    return [
        Doc("household-plan.pdf", pdf, "Household financial plan", _pdf(_household_plan(f))),
        Doc("consulting-invoice.pdf", pdf, "Consulting invoice", _pdf(_consulting_invoice(f))),
        Doc("asia-brokerage.pdf", pdf, "Asia brokerage trade note", _pdf(_asia_brokerage(f))),
        Doc("home-lease.pdf", pdf, "Singapore home lease", _pdf(_home_lease(f))),
        Doc(
            "private-tax-memo.pdf",
            pdf,
            "Private tax planning memo",
            _pdf(_private_memo(f)),
            private=True,
        ),
        Doc("kopi-lane-receipt.pdf", pdf, "Kopi Lane receipt", _pdf(_receipt(f))),
        Doc(
            "singapore-import.csv",
            "text/csv",
            "Singapore import rehearsal - unbooked",
            _import_csv(f).encode(),
        ),
        Doc(
            "bank-notification.txt",
            "text/plain",
            "Offline bank notification rehearsal",
            _bank_notification(f).encode(),
        ),
    ]


def file_documents(f: Facts) -> dict[str, str]:
    """filename → attachment id, for every document filed. The receipt's is what the caller
    promotes to a real receipt."""
    from backend.services import vault_changes
    from backend.services.vault import parse_item_id

    out: dict[str, str] = {}
    for doc in documents(f):
        tags = ["demo", "singapore", "private" if doc.private else "evidence"]
        item = vault_changes.add_file(
            doc.filename,
            doc.mime,
            doc.content,
            title=doc.title,
            year=f.today.year,
            private=doc.private,
            note=NOTE,
            tags=tags,
        )
        out[doc.filename] = parse_item_id(item["id"])[1]
    return out
