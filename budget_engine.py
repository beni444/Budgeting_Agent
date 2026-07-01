import json
import csv
import re
from pathlib import Path
from decimal import Decimal, InvalidOperation
from collections import defaultdict
from difflib import SequenceMatcher


CONFIG_FILE = "budget_config.json"
LEARNED_RULES_FILE = "learned_rules.json"

OUTPUT_CSV = "budget_report_transactions.csv"
OUTPUT_SUMMARY_JSON = "budget_summary.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(data, path):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def load_optional_json(path, default_value):
    if not Path(path).exists():
        return default_value

    try:
        return load_json(path)
    except Exception:
        return default_value


def to_decimal(value):
    if value is None:
        return Decimal("0.00")

    value = str(value).replace(",", "").strip()

    if value == "":
        return Decimal("0.00")

    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0.00")


def normalize_text(value):
    return str(value or "").lower().strip()


def contains_any_keyword(text, keywords):
    text = normalize_text(text)

    for keyword in keywords:
        keyword = normalize_text(keyword)

        if keyword and keyword in text:
            return True

    return False


def load_all_transactions(config):
    all_transactions = []

    for account in config["accounts"]:
        account_id = account["account_id"]
        account_name = account["display_name"]
        is_main = account.get("is_main_income_account", False)
        transaction_file = account["transaction_file"]

        if not Path(transaction_file).exists():
            print(f"Missing transaction file: {transaction_file}")
            continue

        transactions = load_json(transaction_file)

        for tx in transactions:
            debit = to_decimal(tx.get("debit"))
            credit = to_decimal(tx.get("credit"))

            normalized = {
                "account_id": account_id,
                "account_name": account_name,
                "is_main_income_account": is_main,
                "date": tx.get("date", ""),
                "description": tx.get("description", ""),
                "debit": debit,
                "credit": credit,
                "balance": to_decimal(tx.get("balance")),
                "raw_text": tx.get("raw_text", ""),
                "parser_needs_review": tx.get("needs_review", "no"),
                "parser_review_reasons": tx.get("review_reasons", ""),

                "detected_internal_transfer": False,
                "possible_external_transfer": False,
                "transfer_group_id": "",
                "transfer_match_status": "",
                "transfer_counterparty_account": ""
            }

            all_transactions.append(normalized)

    return all_transactions


def filter_budget_period(transactions, start_date, end_date):
    return [
        tx for tx in transactions
        if start_date <= str(tx["date"]) <= end_date
    ]


def get_budget_period(config):
    period = config.get("budget_period", {})

    start_date = period.get("start_date", "").strip()
    end_date = period.get("end_date", "").strip()

    if not start_date:
        start_date = input("Enter budget start date, example 2025-10-01: ").strip()

    if not end_date:
        end_date = input("Enter budget end date, example 2025-10-31: ").strip()

    if not start_date or not end_date:
        raise ValueError("Budget start date and end date are required.")

    if start_date > end_date:
        raise ValueError("Start date cannot be after end date.")

    return start_date, end_date


def clean_transfer_reference(value):
    text = normalize_text(value)

    remove_tokens = [
        "vdc",
        "vdp",
        "pos",
        "posc",
        "d/d",
        "txn",
        "txndate",
        "www.aib.ie",
        "standardconditions",
        "allied",
        "irish",
        "banks",
        "regulated",
        "p.l.c",
        "plc"
    ]

    for token in remove_tokens:
        text = text.replace(token, " ")

    text = re.sub(r"\b\d{1,2}\s?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", " ", text)
    text = re.sub(r"\b\d+\.\d{2}\b", " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9* ]+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def transfer_similarity_score(tx_a, tx_b):
    text_a = clean_transfer_reference(f"{tx_a['description']} {tx_a['raw_text']}")
    text_b = clean_transfer_reference(f"{tx_b['description']} {tx_b['raw_text']}")

    if not text_a or not text_b:
        return 0.0

    sequence_score = SequenceMatcher(None, text_a, text_b).ratio()

    words_a = set(word for word in text_a.split() if len(word) >= 3)
    words_b = set(word for word in text_b.split() if len(word) >= 3)

    if words_a and words_b:
        overlap_score = len(words_a.intersection(words_b)) / max(len(words_a.union(words_b)), 1)
    else:
        overlap_score = 0.0

    return max(sequence_score, overlap_score)


def get_transfer_keywords(config):
    internal_keywords = config.get("rules", {}).get("internal_transfer_keywords", [])
    own_account_keywords = config.get("rules", {}).get("own_account_transfer_keywords", [])

    return internal_keywords + own_account_keywords


def is_transfer_like(tx, config):
    text = normalize_text(f"{tx['description']} {tx['raw_text']}")
    keywords = get_transfer_keywords(config)

    return contains_any_keyword(text, keywords)


def amounts_match(amount_a, amount_b, tolerance=Decimal("0.00")):
    return abs(amount_a - amount_b) <= tolerance


def mark_transfer_matches(transactions, config):
    amount_tolerance = Decimal(
        str(
            config.get("transfer_matching", {}).get("amount_tolerance", "0.00")
        )
    )

    similarity_threshold = float(
        config.get("transfer_matching", {}).get("description_similarity_threshold", 0.50)
    )

    debits = [
        tx for tx in transactions
        if tx["debit"] > 0
    ]

    credits = [
        tx for tx in transactions
        if tx["credit"] > 0
    ]

    candidate_pairs = []

    for debit_tx in debits:
        for credit_tx in credits:
            if debit_tx["account_id"] == credit_tx["account_id"]:
                continue

            if debit_tx["date"] != credit_tx["date"]:
                continue

            if not amounts_match(debit_tx["debit"], credit_tx["credit"], amount_tolerance):
                continue

            debit_is_transfer_like = is_transfer_like(debit_tx, config)
            credit_is_transfer_like = is_transfer_like(credit_tx, config)

            similarity = transfer_similarity_score(debit_tx, credit_tx)

            should_pair = (
                debit_is_transfer_like
                or credit_is_transfer_like
                or similarity >= similarity_threshold
            )

            if should_pair:
                candidate_pairs.append({
                    "debit_tx": debit_tx,
                    "credit_tx": credit_tx,
                    "similarity": similarity,
                    "debit_is_transfer_like": debit_is_transfer_like,
                    "credit_is_transfer_like": credit_is_transfer_like
                })

    candidate_pairs.sort(
        key=lambda pair: (
            pair["debit_is_transfer_like"] or pair["credit_is_transfer_like"],
            pair["similarity"]
        ),
        reverse=True
    )

    used_transaction_ids = set()
    transfer_group_number = 1

    for pair in candidate_pairs:
        debit_tx = pair["debit_tx"]
        credit_tx = pair["credit_tx"]

        debit_object_id = id(debit_tx)
        credit_object_id = id(credit_tx)

        if debit_object_id in used_transaction_ids or credit_object_id in used_transaction_ids:
            continue

        transfer_group_id = f"internal_transfer_{transfer_group_number}"

        debit_tx["detected_internal_transfer"] = True
        debit_tx["transfer_group_id"] = transfer_group_id
        debit_tx["transfer_match_status"] = "matched_included_account_debit"
        debit_tx["transfer_counterparty_account"] = credit_tx["account_name"]

        credit_tx["detected_internal_transfer"] = True
        credit_tx["transfer_group_id"] = transfer_group_id
        credit_tx["transfer_match_status"] = "matched_included_account_credit"
        credit_tx["transfer_counterparty_account"] = debit_tx["account_name"]

        used_transaction_ids.add(debit_object_id)
        used_transaction_ids.add(credit_object_id)

        transfer_group_number += 1

    for tx in transactions:
        if tx["detected_internal_transfer"]:
            continue

        if is_transfer_like(tx, config):
            tx["possible_external_transfer"] = True

            if tx["debit"] > 0:
                tx["transfer_match_status"] = "transfer_out_no_matching_included_credit"
            elif tx["credit"] > 0:
                tx["transfer_match_status"] = "transfer_in_no_matching_included_debit"


def detect_salary_or_income(tx, config):
    combined_text = normalize_text(f"{tx['description']} {tx['raw_text']}")

    return contains_any_keyword(
        combined_text,
        config["rules"].get("salary_keywords", [])
    )


def should_ignore(tx, config):
    combined_text = normalize_text(f"{tx['description']} {tx['raw_text']}")

    return contains_any_keyword(
        combined_text,
        config["rules"].get("ignore_keywords", [])
    )


def extract_keywords_from_description(description):
    description = normalize_text(description)

    description = description.replace(" and ", ",")
    description = description.replace("/", ",")
    description = description.replace(";", ",")
    description = description.replace("&", ",")

    raw_parts = re.split(r"[,\n]", description)

    stop_words = {
        "etc",
        "stuff",
        "things",
        "monthly",
        "each",
        "month",
        "paid",
        "for",
        "the",
        "and",
        "or",
        "like",
        "category",
        "internal",
        "transfer",
        "includes",
        "include",
        "account",
        "accounts"
    }

    keywords = []

    for part in raw_parts:
        cleaned = part.strip().lower()

        if not cleaned:
            continue

        words = cleaned.split()

        phrase = " ".join(
            word for word in words
            if word not in stop_words
        ).strip()

        if phrase:
            keywords.append(phrase)

    return keywords


def find_category_by_id_or_name(config, category_id, category_name, subcategory_name):
    for category in config.get("categories", []):
        category_id_matches = category_id and category.get("category_id") == category_id
        category_name_matches = category_name and normalize_text(category.get("name")) == normalize_text(category_name)

        if category_id_matches or category_name_matches:
            for subcategory in category.get("subcategories", []):
                if normalize_text(subcategory.get("name")) == normalize_text(subcategory_name):
                    return {
                        "category_id": category.get("category_id", ""),
                        "category_name": category.get("name", ""),
                        "subcategory": subcategory.get("name", "")
                    }

    return None


def categorize_with_learned_rules(tx, config):
    learned_rules = load_optional_json(LEARNED_RULES_FILE, [])

    text = normalize_text(f"{tx['description']} {tx['raw_text']}")

    for rule in learned_rules:
        merchant_pattern = normalize_text(rule.get("merchant_pattern", ""))

        if not merchant_pattern:
            continue

        if merchant_pattern in text:
            category_match = find_category_by_id_or_name(
                config=config,
                category_id=rule.get("category_id", ""),
                category_name=rule.get("category_name", ""),
                subcategory_name=rule.get("subcategory", "")
            )

            if category_match:
                return {
                    "category_id": category_match["category_id"],
                    "category_name": category_match["category_name"],
                    "subcategory": category_match["subcategory"],
                    "matched_keyword": merchant_pattern,
                    "category_needs_review": "no",
                    "categorization_method": "learned_rule",
                    "ai_confidence": "",
                    "ai_error": ""
                }

    return None


def categorize_with_explicit_keywords(tx, config):
    text = normalize_text(f"{tx['description']} {tx['raw_text']}")

    for category in config.get("categories", []):
        for subcategory in category.get("subcategories", []):
            explicit_keywords = subcategory.get("keywords", [])

            for keyword in explicit_keywords:
                keyword = normalize_text(keyword)

                if keyword and keyword in text:
                    return {
                        "category_id": category["category_id"],
                        "category_name": category["name"],
                        "subcategory": subcategory["name"],
                        "matched_keyword": keyword,
                        "category_needs_review": "no",
                        "categorization_method": "explicit_keyword",
                        "ai_confidence": "",
                        "ai_error": ""
                    }

    return None


def categorize_with_ai(tx, config):
    try:
        from ai_categorizer import ai_categorize_transaction

        ai_result = ai_categorize_transaction(
            description=tx["description"],
            config=config
        )

        ai_result.setdefault("matched_keyword", "")
        ai_result.setdefault("category_needs_review", "yes")
        ai_result.setdefault("categorization_method", "ai_embedding")
        ai_result.setdefault("ai_confidence", "")
        ai_result.setdefault("ai_error", "")

        return ai_result

    except Exception as error:
        return {
            "category_id": "uncategorized",
            "category_name": "Uncategorized",
            "subcategory": "Uncategorized",
            "matched_keyword": "",
            "category_needs_review": "yes",
            "categorization_method": "ai_failed",
            "ai_confidence": "",
            "ai_error": str(error)
        }


def categorize_with_description_keywords(tx, config):
    text = normalize_text(f"{tx['description']} {tx['raw_text']}")

    for category in config.get("categories", []):
        for subcategory in category.get("subcategories", []):
            generated_keywords = extract_keywords_from_description(
                subcategory.get("description", "")
            )

            for keyword in generated_keywords:
                keyword = normalize_text(keyword)

                if keyword and keyword in text:
                    return {
                        "category_id": category["category_id"],
                        "category_name": category["name"],
                        "subcategory": subcategory["name"],
                        "matched_keyword": keyword,
                        "category_needs_review": "no",
                        "categorization_method": "description_keyword_fallback",
                        "ai_confidence": "",
                        "ai_error": ""
                    }

    return {
        "category_id": "uncategorized",
        "category_name": "Uncategorized",
        "subcategory": "Uncategorized",
        "matched_keyword": "",
        "category_needs_review": "yes",
        "categorization_method": "uncategorized",
        "ai_confidence": "",
        "ai_error": ""
    }


def categorize_transaction(tx, config):
    learned_result = categorize_with_learned_rules(tx, config)
    if learned_result:
        return learned_result

    explicit_keyword_result = categorize_with_explicit_keywords(tx, config)
    if explicit_keyword_result:
        return explicit_keyword_result

    ai_result = categorize_with_ai(tx, config)

    if ai_result.get("category_name") != "Uncategorized":
        return ai_result

    return categorize_with_description_keywords(tx, config)


def classify_transaction(tx, config):
    if should_ignore(tx, config):
        return "ignored"

    if tx.get("detected_internal_transfer"):
        return "internal_transfer"

    if tx.get("possible_external_transfer"):
        if tx["debit"] > 0:
            return "external_transfer_out"

        if tx["credit"] > 0:
            return "external_transfer_in"

    if tx["credit"] > 0:
        if tx["is_main_income_account"] or detect_salary_or_income(tx, config):
            return "income"

        return "income"

    if tx["debit"] > 0:
        return "expense"

    return "unknown"


def process_transactions(transactions, config):
    processed = []

    for index, tx in enumerate(transactions):
        transaction_type = classify_transaction(tx, config)

        category_result = {
            "category_id": "",
            "category_name": "",
            "subcategory": "",
            "matched_keyword": "",
            "category_needs_review": "no",
            "categorization_method": "",
            "ai_confidence": "",
            "ai_error": ""
        }

        amount = Decimal("0.00")
        include_in_expenses = "no"
        review_decision = ""

        if transaction_type == "expense":
            amount = tx["debit"]
            include_in_expenses = "yes"
            category_result = categorize_transaction(tx, config)

        elif transaction_type == "income":
            amount = tx["credit"]

        elif transaction_type == "internal_transfer":
            amount = tx["debit"] if tx["debit"] > 0 else tx["credit"]
            category_result = {
                "category_id": "",
                "category_name": "Internal Transfer",
                "subcategory": "Between Included Accounts",
                "matched_keyword": "",
                "category_needs_review": "no",
                "categorization_method": "transfer_pair_match",
                "ai_confidence": "",
                "ai_error": ""
            }

        elif transaction_type == "external_transfer_out":
            amount = tx["debit"]
            include_in_expenses = "review"
            review_decision = "needs_user_decision"
            category_result = {
                "category_id": "",
                "category_name": "Transfer to Other Account",
                "subcategory": "External Transfer Out",
                "matched_keyword": "",
                "category_needs_review": "yes",
                "categorization_method": "unmatched_transfer_out",
                "ai_confidence": "",
                "ai_error": ""
            }

        elif transaction_type == "external_transfer_in":
            amount = tx["credit"]
            include_in_expenses = "no"
            review_decision = "needs_user_decision"
            category_result = {
                "category_id": "",
                "category_name": "Transfer from Other Account",
                "subcategory": "External Transfer In",
                "matched_keyword": "",
                "category_needs_review": "yes",
                "categorization_method": "unmatched_transfer_in",
                "ai_confidence": "",
                "ai_error": ""
            }

        category_needs_review = category_result.get("category_needs_review", "no")
        categorization_method = category_result.get("categorization_method", "")
        category_name = category_result.get("category_name", "")
        ai_error = category_result.get("ai_error", "")

        review_reasons = []

        if tx["parser_review_reasons"]:
            review_reasons.append(tx["parser_review_reasons"])

        if category_needs_review == "yes" and category_name == "Uncategorized":
            review_reasons.append("Uncategorized")

        if categorization_method == "ai_embedding" and category_needs_review == "yes":
            review_reasons.append("AI categorized - please review")

        if categorization_method == "ai_failed":
            review_reasons.append(f"AI categorization failed: {ai_error}")

        if transaction_type == "external_transfer_out":
            review_reasons.append("Transfer to other account - choose whether to include in final expenses")

        if transaction_type == "external_transfer_in":
            review_reasons.append("Transfer-like credit from account not included in budget - please review")

        if transaction_type == "unknown":
            review_reasons.append("Unknown transaction type")

        processed_tx = {
            "transaction_id": f"tx_{index + 1}",
            "date": tx["date"],
            "account_id": tx["account_id"],
            "account_name": tx["account_name"],
            "description": tx["description"],
            "debit": str(tx["debit"]),
            "credit": str(tx["credit"]),
            "amount": str(amount),
            "transaction_type": transaction_type,
            "category_id": category_result.get("category_id", ""),
            "category_name": category_result.get("category_name", ""),
            "subcategory": category_result.get("subcategory", ""),
            "matched_keyword": category_result.get("matched_keyword", ""),
            "categorization_method": categorization_method,
            "ai_confidence": category_result.get("ai_confidence", ""),
            "ai_error": ai_error,
            "transfer_group_id": tx.get("transfer_group_id", ""),
            "transfer_match_status": tx.get("transfer_match_status", ""),
            "transfer_counterparty_account": tx.get("transfer_counterparty_account", ""),
            "include_in_expenses": include_in_expenses,
            "review_decision": review_decision,
            "needs_review": (
                "yes"
                if tx["parser_needs_review"] == "yes"
                or category_needs_review == "yes"
                or transaction_type == "unknown"
                else "no"
            ),
            "review_reasons": "; ".join(reason for reason in review_reasons if reason),
            "raw_text": tx["raw_text"]
        }

        processed.append(processed_tx)

    return processed


def create_summary(processed, config, start_date, end_date):
    total_income = Decimal("0.00")
    total_category_expenses = Decimal("0.00")
    total_external_transfers_out_review = Decimal("0.00")
    total_external_transfers_out_included = Decimal("0.00")
    total_external_transfers_out_excluded = Decimal("0.00")
    total_external_transfers_in = Decimal("0.00")
    total_internal_transfers = Decimal("0.00")

    category_totals = defaultdict(Decimal)
    subcategory_totals = defaultdict(Decimal)

    uncategorized_count = 0
    review_count = 0

    categorization_method_counts = defaultdict(int)

    for tx in processed:
        amount = to_decimal(tx["amount"])

        method = tx.get("categorization_method", "")
        if method:
            categorization_method_counts[method] += 1

        if tx["transaction_type"] == "income":
            total_income += amount

        elif tx["transaction_type"] == "expense":
            total_category_expenses += amount

            category_name = tx["category_name"] or "Uncategorized"
            subcategory_name = tx["subcategory"] or "Uncategorized"

            category_totals[category_name] += amount
            subcategory_totals[f"{category_name} > {subcategory_name}"] += amount

        elif tx["transaction_type"] == "external_transfer_out":
            if tx.get("include_in_expenses") == "yes":
                total_external_transfers_out_included += amount
            elif tx.get("include_in_expenses") == "no":
                total_external_transfers_out_excluded += amount
            else:
                total_external_transfers_out_review += amount

        elif tx["transaction_type"] == "external_transfer_in":
            total_external_transfers_in += amount

        elif tx["transaction_type"] == "internal_transfer":
            total_internal_transfers += amount

        if tx["category_name"] == "Uncategorized":
            uncategorized_count += 1

        if tx["needs_review"] == "yes":
            review_count += 1

    total_external_transfers_to_other_accounts = (
        total_external_transfers_out_review
        + total_external_transfers_out_included
        + total_external_transfers_out_excluded
    )

    total_expenses = total_category_expenses + total_external_transfers_out_included

    planned_savings = to_decimal(config.get("planned_savings", 0))
    actual_savings = total_income - total_expenses
    savings_difference = actual_savings - planned_savings

    category_limit_comparison = []

    for category in config["categories"]:
        category_name = category["name"]
        planned_limit = to_decimal(category.get("monthly_limit", 0))
        actual_spend = category_totals[category_name]
        remaining = planned_limit - actual_spend

        category_limit_comparison.append({
            "category_name": category_name,
            "planned_limit": str(planned_limit),
            "actual_spend": str(actual_spend),
            "remaining": str(remaining),
            "status": "over_limit" if remaining < 0 else "within_limit"
        })

    subcategory_limit_comparison = []

    for category in config.get("categories", []):
        category_name = category.get("name", "")

        for subcategory in category.get("subcategories", []):
            subcategory_name = subcategory.get("name", "")
            planned_limit_raw = subcategory.get("monthly_limit", "")

            key = f"{category_name} > {subcategory_name}"
            actual_spend = subcategory_totals[key]

            has_limit = str(planned_limit_raw).strip() != ""
            planned_limit = to_decimal(planned_limit_raw) if has_limit else Decimal("0.00")

            if has_limit:
                remaining = planned_limit - actual_spend
                status = "over_limit" if remaining < 0 else "within_limit"
            else:
                remaining = ""
                status = "no_limit"

            subcategory_limit_comparison.append({
                "category_name": category_name,
                "subcategory_name": subcategory_name,
                "planned_limit": str(planned_limit) if has_limit else "",
                "actual_spend": str(actual_spend),
                "remaining": str(remaining) if has_limit else "",
                "status": status
            })

    summary = {
        "budget_start_date": start_date,
        "budget_end_date": end_date,
        "total_income": str(total_income),
        "total_expenses": str(total_expenses),
        "total_category_expenses": str(total_category_expenses),
        "total_external_transfers_to_other_accounts": str(total_external_transfers_to_other_accounts),
        "total_external_transfers_to_other_accounts_needing_review": str(total_external_transfers_out_review),
        "total_external_transfers_to_other_accounts_included_as_expense": str(total_external_transfers_out_included),
        "total_external_transfers_to_other_accounts_excluded_from_expense": str(total_external_transfers_out_excluded),
        "total_external_transfers_from_other_accounts_ignored_from_income": str(total_external_transfers_in),
        "total_internal_transfers_ignored_from_spending": str(total_internal_transfers),
        "planned_savings": str(planned_savings),
        "actual_savings": str(actual_savings),
        "savings_difference": str(savings_difference),
        "category_totals": {
            key: str(value) for key, value in category_totals.items()
        },
        "subcategory_totals": {
            key: str(value) for key, value in subcategory_totals.items()
        },
        "category_limit_comparison": category_limit_comparison,
        "subcategory_limit_comparison": subcategory_limit_comparison,
        "uncategorized_count": uncategorized_count,
        "review_count": review_count,
        "categorization_method_counts": {
            key: value for key, value in categorization_method_counts.items()
        }
    }

    return summary


def save_processed_csv(processed, output_path):
    fieldnames = [
        "transaction_id",
        "date",
        "account_id",
        "account_name",
        "description",
        "debit",
        "credit",
        "amount",
        "transaction_type",
        "category_id",
        "category_name",
        "subcategory",
        "matched_keyword",
        "categorization_method",
        "ai_confidence",
        "ai_error",
        "transfer_group_id",
        "transfer_match_status",
        "transfer_counterparty_account",
        "include_in_expenses",
        "review_decision",
        "needs_review",
        "review_reasons",
        "raw_text"
    ]

    with open(output_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            extrasaction="ignore"
        )

        writer.writeheader()

        for tx in processed:
            writer.writerow(tx)


def main():
    config = load_json(CONFIG_FILE)

    start_date, end_date = get_budget_period(config)

    config["budget_period"] = {
        "start_date": start_date,
        "end_date": end_date
    }

    all_transactions = load_all_transactions(config)

    period_transactions = filter_budget_period(
        all_transactions,
        start_date,
        end_date
    )

    mark_transfer_matches(period_transactions, config)

    processed = process_transactions(period_transactions, config)

    summary = create_summary(
        processed,
        config,
        start_date,
        end_date
    )

    save_processed_csv(processed, OUTPUT_CSV)
    save_json(summary, OUTPUT_SUMMARY_JSON)

    print(f"Transactions loaded: {len(all_transactions)}")
    print(f"Transactions in budget period {start_date} to {end_date}: {len(period_transactions)}")
    print(f"Processed transactions saved to: {OUTPUT_CSV}")
    print(f"Budget summary saved to: {OUTPUT_SUMMARY_JSON}")

    print("\nSummary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()