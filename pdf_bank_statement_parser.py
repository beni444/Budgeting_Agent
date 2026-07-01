#!/usr/bin/env python3
"""
Bank statement PDF parser: layout-first, LLM-last.

Usage:
  python bank_statement_parser_hybrid.py statement.pdf --csv transactions.csv --json transactions.json

Install:
  pip install pdfplumber pandas python-dateutil

This parser is designed for digitally-generated PDF statements with a text layer.
For scanned statements, OCR first with a layout-preserving tool, then feed the OCR PDF here.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

import pdfplumber
from dateutil import parser as date_parser

DATE_RE = re.compile(
    r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b"
)
# Accept both grouped and ungrouped thousands: 1,234.56 and 1234.56.
# Bank 2/AIB-style statements often emit 2551.74 instead of 2,551.74.
MONEY_NUMBER_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+)"
AMOUNT_RE = re.compile(rf"(?<!\w)[€£$]?\s*\(?-?{MONEY_NUMBER_RE}(?:\.\d{{1,2}})\)?(?!\w)")
# For layout assignment only: require a full money-like token.
# This avoids treating account/ref numbers or dates as transaction amounts.
AMOUNT_TOKEN_RE = re.compile(rf"^[€£$]?\s*\(?-?{MONEY_NUMBER_RE}(?:\.\d{{1,2}})\)?$")
OVERDRAFT_RE = re.compile(r"\b(?:OD|OVERDRAFT)\b", re.I)
IBAN_OR_REF_RE = re.compile(r"^(IE\d+|GB\d+|TxnDate:|Ref:|Reference:|Mandate:|Card ending)", re.I)
SKIP_RE = re.compile(r"^(balance forward|opening balance|closing balance|brought forward)$", re.I)


@dataclass
class Txn:
    date: str
    description: str
    debit: str = ""
    credit: str = ""
    balance: str = ""
    needs_review: str = "no"
    review_reasons: str = ""
    page: int = 0
    raw_text: str = ""


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def normalize_header(text: str) -> str:
    """
    Normalize header text so variations like:
    'Debit €', 'DEBIT(€)', 'Money-out', 'Paid In / Credit'
    become easier to match.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = text.lower()

    # Convert currency symbols to words before removing punctuation. 
    text = text.replace("€", " eur ")
    text = text.replace("£", " gbp ")
    text = text.replace("$", " usd ")

    # Normalize punctuation, slashes, hyphens, brackets, etc. - replace anything that's not a alphabet/digit with space
    text = re.sub(r"[^a-z0-9]+", " ", text)

    # Collapse extra spaces.
    text = re.sub(r"\s+", " ", text).strip()

    return text

DEFAULT_HEADERS = {
    "date": [
        "date",
        "transaction date",
        "posting date",
        "value date",
        "book date",
        "process date",
        "entry date",
    ],
    "description": [
        "details",
        "description",
        "narrative",
        "particulars",
        "transaction details",
        "payment details",
        "transaction description",
        "merchant",
        "reference",
    ],
    "debit": [
        "debit",
        "debit eur",
        "money out",
        "money out eur",
        "paid out",
        "paid out eur",
        "payment out",
        "payments out",
        "withdrawal",
        "withdrawals",
        "amount debited",
    ],
    "credit": [
        "credit",
        "credit eur",
        "money in",
        "money in eur",
        "paid in",
        "paid in eur",
        "payment in",
        "payments in",
        "deposit",
        "deposits",
        "amount credited",
    ],
    "balance": [
        "balance",
        "balance eur",
        "running balance",
        "closing balance",
        "account balance",
        "available balance",
        "balance after transaction",
    ],
}

DEFAULT_HEADERS = {
    key: [normalize_header(alias) for alias in aliases]
    for key, aliases in DEFAULT_HEADERS.items()
}
    
def has_overdraft_marker(value: str) -> bool:
    """Return True when a balance cell marks the amount as overdraft."""
    return bool(OVERDRAFT_RE.search(norm(value)))


def money_to_decimal(value: str, overdraft_is_negative: bool = False) -> Decimal | None:
    """
    Convert a money string to Decimal.

    For balance cells, pass overdraft_is_negative=True so values like
    "3.85 OD", "3.85 od", or "3.85 overdraft" become Decimal("-3.85").
    Plain negative formats like "-3.85" and accounting negatives like
    "(3.85)" are also treated as negative.
    """
    original = norm(value)

    if not original:
        return None

    cleaned = (
        original.replace(",", "")
        .replace("€", "")
        .replace("£", "")
        .replace("$", "")
        .strip()
    )

    match = re.search(r"-?\d+(?:\.\d{1,2})?", cleaned)

    if not match:
        return None

    try:
        amount = Decimal(match.group(0))
    except InvalidOperation:
        return None

    accounting_negative = bool(
        re.search(r"\(\s*-?\d+(?:\.\d{1,2})?\s*\)", cleaned)
    )
    overdraft_negative = overdraft_is_negative and has_overdraft_marker(original)

    # Avoid double-negating values that already include a leading minus sign.
    if (accounting_negative or overdraft_negative) and amount > 0:
        amount = -amount

    return amount


def is_balance_forward_description(description: str) -> bool:
    return normalize_header(description) in {
        "balance forward",
        "opening balance",
        "brought forward",
        "balance brought forward",
    }


# Do not use description keywords to decide debit vs credit.
# The output direction must come from the PDF columns; balance checks only flag review issues.

def parse_amount(value: str, overdraft_is_negative: bool = False) -> str:
    amount = money_to_decimal(value, overdraft_is_negative=overdraft_is_negative)

    if amount is None:
        return ""

    return f"{amount:.2f}"


def find_amounts(s: str, overdraft_is_negative: bool = False) -> list[str]:
    """
    Extract money amounts from a cell.

    For balance cells, pass overdraft_is_negative=True. The overdraft marker
    may be outside the numeric regex match, e.g. "3.85 OD", so we preserve
    the full cell context when parsing each matched amount.
    """
    cell = s or ""
    overdraft_context = overdraft_is_negative and has_overdraft_marker(cell)
    amounts: list[str] = []

    for match in AMOUNT_RE.finditer(cell):
        matched_text = match.group(0)

        if overdraft_context:
            matched_text = f"{matched_text} OD"

        parsed = parse_amount(
            matched_text,
            overdraft_is_negative=overdraft_is_negative,
        )

        if parsed:
            amounts.append(parsed)

    return amounts


def is_amount_token(text: str) -> bool:
    """True only for a single PDF word that looks like a money amount."""
    return bool(AMOUNT_TOKEN_RE.fullmatch(norm(text)))


def parse_date(s: str, dayfirst: bool = True) -> str:
    m = DATE_RE.search(s or "")
    if not m:
        return ""
    try:
        dt = date_parser.parse(m.group(1), dayfirst=dayfirst, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        return ""


def group_words_into_rows(words: list[dict], y_tol: float = 3.0) -> list[list[dict]]:
    """Cluster pdfplumber words into visual rows by vertical position."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        if not rows or abs(rows[-1][0]["top"] - w["top"]) > y_tol:
            rows.append([w])
        else:
            rows[-1].append(w)
    return [sorted(r, key=lambda x: x["x0"]) for r in rows]


def row_text(row: list[dict]) -> str:
    return norm(" ".join(w["text"] for w in row))


def locate_header(rows: list[list[dict]]) -> tuple[int, dict[str, float]] | None:
    """
    Find transaction table header and estimate column x positions.

    Handles headers like:
    Date | Details | Debit € | Credit € | Balance €
    Date | Transaction details | Payments - out | Payments - in | Balance
    """
    for i, row in enumerate(rows):
        normalized_row = normalize_header(row_text(row))

        has_date = any(alias in normalized_row for alias in DEFAULT_HEADERS["date"])
        has_description = any(alias in normalized_row for alias in DEFAULT_HEADERS["description"])
        has_balance = any(alias in normalized_row for alias in DEFAULT_HEADERS["balance"])

        if not (has_date and has_description and has_balance):
            continue

        xs: dict[str, float] = {}

        # Build word phrases from the header row.
        # This allows "Payments - out" to become "payments out".
        max_phrase_len = 5

        for key, aliases in DEFAULT_HEADERS.items():
            hits = []

            for start in range(len(row)):
                words = []
                phrase_x0 = row[start]["x0"]

                for end in range(start, min(start + max_phrase_len, len(row))):
                    words.append(row[end]["text"])
                    phrase = normalize_header(" ".join(words))

                    if phrase in aliases:
                        hits.append(phrase_x0)

            if hits:
                xs[key] = min(hits)

        # Extra targeted detection for "Payments - out" and "Payments - in"
        # because many banks split this into three separate PDF words.
        for start in range(len(row)):
            phrase_words = []

            for end in range(start, min(start + 5, len(row))):
                phrase_words.append(row[end]["text"])
                phrase = normalize_header(" ".join(phrase_words))

                if phrase in ["payments out", "payment out", "paid out", "money out"]:
                    xs["debit"] = row[start]["x0"]

                if phrase in ["payments in", "payment in", "paid in", "money in"]:
                    xs["credit"] = row[start]["x0"]

        required = {"date", "description", "debit", "credit", "balance"}

        if required.issubset(xs):
            return i, xs

    return None

def make_boundaries(xs: dict[str, float], page_width: float) -> dict[str, tuple[float, float]]:
    """
    Treat detected header x positions as the START of each column.

    This works better for bank statements because:
    - Date values can extend close to the Details header.
    - Amount values are usually right-aligned inside their columns.
    - Header text is often not centered over the numeric values.
    """
    required = ["date", "description", "debit", "credit", "balance"]

    missing = [key for key in required if key not in xs]
    if missing:
        raise ValueError(f"Missing column x positions: {missing}")

    return {
        "date": (0, xs["description"]),
        "description": (xs["description"], xs["debit"]),
        "debit": (xs["debit"], xs["credit"]),
        "credit": (xs["credit"], xs["balance"]),
        "balance": (xs["balance"], page_width),
    }

def split_row_by_columns(row: list[dict], boundaries: dict[str, tuple[float, float]]) -> dict[str, str]:
    """
    Assign words to columns.

    Text words use their center point. Money-like tokens use their right edge,
    because bank-statement amounts are commonly right-aligned inside narrow
    debit/credit/balance columns. This is especially important for AIB-style
    layouts where the Debit € and Credit € columns are only ~55-60 PDF units
    wide.
    """
    cells = {
        "date": [],
        "description": [],
        "debit": [],
        "credit": [],
        "balance": [],
    }

    for word in row:
        text = word["text"]
        x0 = float(word["x0"])
        x1 = float(word["x1"])
        x_center = (x0 + x1) / 2

        # Amounts are right-aligned; descriptions/dates are not.
        # Use x1 for amount-like words so a credit amount whose x0 spills left
        # into the debit band is still assigned by the column it visually ends in.
        x_anchor = x1 if is_amount_token(text) else x_center

        assigned = False

        for key, (left, right) in boundaries.items():
            if left <= x_anchor < right:
                cells[key].append(text)
                assigned = True
                break

        # If no column matched, append to description rather than dropping it.
        if not assigned:
            cells["description"].append(text)

    return {
        key: norm(" ".join(value))
        for key, value in cells.items()
    }

def looks_like_table_continuation(cells: dict[str, str]) -> bool:
    txt = " ".join(cells.values()).lower()
    if not txt:
        return False
    if any(h in txt for h in ["page ", "statement", "account number", "iban", "important information"]):
        return False
    return True


def add_review(tx: Txn, reason: str) -> None:
    tx.needs_review = "yes"
    tx.review_reasons = f"{tx.review_reasons}; {reason}".strip("; ")


def add_balance_review_if_needed(
    tx: Txn,
    expected_balance: Decimal,
    actual_balance: Decimal,
    tolerance: Decimal = Decimal("0.01"),
) -> None:
    """
    Validate the running balance without changing debit/credit.

    If the opposite sign would reconcile, flag that explicitly for review, but
    keep the extracted debit/credit exactly as the column splitter produced it.
    """
    if abs(expected_balance - actual_balance) <= tolerance:
        return

    debit = money_to_decimal(tx.debit) or Decimal("0.00")
    credit = money_to_decimal(tx.credit) or Decimal("0.00")

    if debit and not credit:
        opposite_expected = expected_balance + (debit * 2)
        if abs(opposite_expected - actual_balance) <= tolerance:
            add_review(
                tx,
                "Balance check suggests this debit-column amount may visually belong to credit; left unchanged",
            )
            return

    if credit and not debit:
        opposite_expected = expected_balance - (credit * 2)
        if abs(opposite_expected - actual_balance) <= tolerance:
            add_review(
                tx,
                "Balance check suggests this credit-column amount may visually belong to debit; left unchanged",
            )
            return

    add_review(tx, f"Balance check failed: expected {expected_balance:.2f}")

def parse_pdf(
    pdf_path: Path,
    dayfirst: bool = True,
    y_tol: float = 3.0,
    debug: bool = False,
    debug_words: bool = False,
) -> list[Txn]:
    transactions: list[Txn] = []
    current_date = ""
    running_balance: Decimal | None = None

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                x_tolerance=1.5,
                y_tolerance=2.0,
                keep_blank_chars=False
            ) or []

            rows = group_words_into_rows(words, y_tol=y_tol)
            header = locate_header(rows)

            if not header:
                continue

            header_idx, xs = header
            bounds = make_boundaries(xs, float(page.width))

            if debug:
                print(f"\nPage {page_no}")
                print("Header row:", row_text(rows[header_idx]))
                print("Detected xs:", xs)
                print("Boundaries:", bounds)
                print("\nFirst 15 rows after header:")

                for debug_row in rows[header_idx + 1: header_idx + 16]:
                    print("RAW:", row_text(debug_row))
                    print("SPLIT:", split_row_by_columns(debug_row, bounds))
                    if debug_words:
                        print("WORDS:", [
                            {
                                "text": w["text"],
                                "x0": round(float(w["x0"]), 2),
                                "x1": round(float(w["x1"]), 2),
                                "anchor": round(float(w["x1"] if is_amount_token(w["text"]) else (float(w["x0"]) + float(w["x1"])) / 2), 2),
                            }
                            for w in debug_row
                        ])

            for row in rows[header_idx + 1:]:
                cells = split_row_by_columns(row, bounds)

                if not looks_like_table_continuation(cells):
                    continue

                date_found = parse_date(cells["date"], dayfirst=dayfirst)

                if date_found:
                    current_date = date_found

                description = norm(cells["description"])

                debit_vals = find_amounts(cells["debit"])
                credit_vals = find_amounts(cells["credit"])
                balance_vals = find_amounts(cells["balance"], overdraft_is_negative=True)

                debit = debit_vals[-1] if debit_vals else ""
                credit = credit_vals[-1] if credit_vals else ""
                balance = balance_vals[-1] if balance_vals else ""

                raw = row_text(row)

                if not description and not debit and not credit and not balance:
                    continue

                if is_balance_forward_description(description):
                    opening_balance = money_to_decimal(balance, overdraft_is_negative=True)

                    if opening_balance is not None:
                        running_balance = opening_balance

                    continue

                if not current_date:
                    continue

                if SKIP_RE.match(description):
                    continue

                if not debit and not credit:
                    if transactions:
                        previous_tx = transactions[-1]

                        if description:
                            previous_tx.description = norm(
                                previous_tx.description + " " + description
                            )

                        if balance:
                            previous_tx.balance = balance

                            actual_balance = money_to_decimal(balance, overdraft_is_negative=True)

                            if running_balance is not None and actual_balance is not None:
                                add_balance_review_if_needed(
                                    previous_tx,
                                    expected_balance=running_balance,
                                    actual_balance=actual_balance,
                                )

                                running_balance = actual_balance

                    continue

                tx = Txn(
                    date=current_date,
                    description=description,
                    debit=debit,
                    credit=credit,
                    balance=balance,
                    page=page_no,
                    raw_text=raw,
                )

                if debit and credit:
                    add_review(tx, "Both debit and credit found")

                if running_balance is not None:
                    debit_dec = money_to_decimal(debit) or Decimal("0.00")
                    credit_dec = money_to_decimal(credit) or Decimal("0.00")

                    expected_balance = running_balance - debit_dec + credit_dec
                    running_balance = expected_balance

                    if balance:
                        actual_balance = money_to_decimal(balance, overdraft_is_negative=True)

                        if actual_balance is not None:
                            add_balance_review_if_needed(
                                tx,
                                expected_balance=expected_balance,
                                actual_balance=actual_balance,
                            )

                            running_balance = actual_balance

                transactions.append(tx)

    return transactions

def validate_running_balance(transactions: list[Txn], tolerance: Decimal = Decimal("0.02")) -> None:
    """Flag rows whose balance does not reconcile with the previous available balance."""
    previous: Optional[Decimal] = None
    for tx in transactions:
        try:
            debit = Decimal(tx.debit) if tx.debit else Decimal("0")
            credit = Decimal(tx.credit) if tx.credit else Decimal("0")
            bal = money_to_decimal(tx.balance, overdraft_is_negative=True) if tx.balance else None
        except InvalidOperation:
            add_review(tx, "Invalid numeric amount")
            continue
        if bal is None:
            continue
        if previous is not None:
            expected = previous - debit + credit
            if abs(expected - bal) > tolerance:
                add_review(tx, f"Balance check failed: expected {expected:.2f}")
        previous = bal


def write_csv(transactions: Iterable[Txn], path: Path) -> None:
    rows = [asdict(t) for t in transactions]
    fieldnames = ["date", "description", "debit", "credit", "balance", "needs_review", "review_reasons", "page", "raw_text"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(transactions: Iterable[Txn], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in transactions], f, indent=2, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--csv", type=Path, default=Path("transactions.csv"))
    ap.add_argument("--json", type=Path, default=Path("transactions.json"))
    ap.add_argument("--month-first", action="store_true", help="Use MM/DD/YYYY-style parsing where ambiguous")
    ap.add_argument("--y-tol", type=float, default=3.0, help="Vertical tolerance for grouping words into rows")
    ap.add_argument("--debug", action="store_true", help="Print detected headers and column positions")
    ap.add_argument("--debug-words", action="store_true", help="Also print word x0/x1/anchor coordinates for debug rows")
    args = ap.parse_args()

    #txs = parse_pdf(args.pdf, dayfirst=not args.month_first, y_tol=args.y_tol)
    txs = parse_pdf(
        args.pdf,
        dayfirst=not args.month_first,
        y_tol=args.y_tol,
        debug=args.debug,
        debug_words=args.debug_words,
    )
    write_csv(txs, args.csv)
    write_json(txs, args.json)
    print(f"Extracted {len(txs)} transactions")
    print(f"CSV:  {args.csv}")
    print(f"JSON: {args.json}")
    
    review = [t for t in txs if t.needs_review == "yes"]
    if review:
        print(f"Needs review: {len(review)}")


if __name__ == "__main__":
    main()
