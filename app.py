import json
import shutil
import uuid
import re
from pathlib import Path
from datetime import datetime

from flask import Flask, render_template, request, send_file, after_this_request, redirect, url_for, flash
from werkzeug.utils import secure_filename

from pdf_bank_statement_parser import parse_pdf, write_json

from budget_engine import (
    load_all_transactions,
    filter_budget_period,
    mark_transfer_matches,
    process_transactions,
    create_summary,
    save_processed_csv,
    save_json,
)

from excel_report import create_excel_report


APP_ROOT = Path(__file__).parent.resolve()

TEMP_RUNS_DIR = APP_ROOT / "temp_runs"
REVIEW_RUNS_DIR = APP_ROOT / "instance" / "review_runs"
DOWNLOADS_DIR = APP_ROOT / "instance" / "downloads"
LEARNED_RULES_FILE = APP_ROOT / "learned_rules.json"

OUTPUT_CSV_NAME = "budget_report_transactions.csv"
OUTPUT_SUMMARY_NAME = "budget_summary.json"

ALLOWED_EXTENSIONS = {"pdf"}


app = Flask(__name__)
app.secret_key = "replace-this-with-a-random-secret-key"

TEMP_RUNS_DIR.mkdir(exist_ok=True)
REVIEW_RUNS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def ensure_learned_rules_file():
    if not LEARNED_RULES_FILE.exists():
        with open(LEARNED_RULES_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, indent=2)


def load_json_file(path, default_value):
    if not Path(path).exists():
        return default_value

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default_value


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def split_comma_input(value):
    return [
        item.strip()
        for item in str(value or "").split(",")
        if item.strip()
    ]


def parse_optional_float(value):
    value = str(value or "").strip()

    if value == "":
        return ""

    try:
        return float(value)
    except ValueError:
        return ""


def slugify(value):
    return (
        str(value)
        .lower()
        .strip()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def build_config_from_form(run_dir):
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    planned_savings = float(request.form.get("planned_savings", "0") or 0)

    if not start_date or not end_date:
        raise ValueError("Budget start date and end date are required.")

    account_count = int(request.form.get("account_count", "1") or 1)
    category_count = int(request.form.get("category_count", "3") or 3)

    accounts = []

    for index in range(1, account_count + 1):
        account_name = request.form.get(f"account_name_{index}", "").strip()
        receives_income = request.form.get(f"account_income_{index}") == "on"

        uploaded_file = request.files.get(f"account_file_{index}")

        if not account_name:
            raise ValueError(f"Account {index} name is required.")

        if uploaded_file is None or uploaded_file.filename == "":
            raise ValueError(f"PDF statement for account {index} is required.")

        if not allowed_file(uploaded_file.filename):
            raise ValueError(f"Account {index} file must be a PDF.")

        safe_filename = secure_filename(uploaded_file.filename)
        pdf_path = run_dir / f"account_{index}_{safe_filename}"
        json_path = run_dir / f"account_{index}_transactions.json"

        uploaded_file.save(pdf_path)

        transactions = parse_pdf(pdf_path)
        write_json(transactions, json_path)

        pdf_path.unlink(missing_ok=True)

        accounts.append({
            "account_id": f"account_{index}_{slugify(account_name)}",
            "display_name": account_name,
            "is_main_income_account": receives_income,
            "transaction_file": str(json_path)
        })

    categories = []

    for category_index in range(1, category_count + 1):
        category_name = request.form.get(f"category_name_{category_index}", "").strip()
        category_color = request.form.get(f"category_color_{category_index}", "").strip()
        monthly_limit = parse_optional_float(request.form.get(f"category_limit_{category_index}", ""))
        subcategory_count = int(request.form.get(f"subcategory_count_{category_index}", "1") or 1)

        if not category_name:
            raise ValueError(f"Category {category_index} name is required.")

        if not category_color:
            category_color = "blue"

        subcategories = []

        for sub_index in range(1, subcategory_count + 1):
            subcategory_name = request.form.get(
                f"subcategory_name_{category_index}_{sub_index}", ""
            ).strip()

            subcategory_description = request.form.get(
                f"subcategory_description_{category_index}_{sub_index}", ""
            ).strip()

            subcategory_limit = parse_optional_float(
                request.form.get(f"subcategory_limit_{category_index}_{sub_index}", "")
            )

            if not subcategory_name:
                continue

            if not subcategory_description:
                subcategory_description = subcategory_name

            subcategories.append({
                "subcategory_id": f"cat_{category_index}_sub_{sub_index}_{slugify(subcategory_name)}",
                "name": subcategory_name,
                "description": subcategory_description,
                "monthly_limit": subcategory_limit,
                "examples": [],
                "keywords": []
            })

        if not subcategories:
            raise ValueError(f"Category {category_name} needs at least one subcategory.")

        subcategory_names = ", ".join(
            subcategory["name"]
            for subcategory in subcategories
        )

        categories.append({
            "category_id": f"cat_{category_index}_{slugify(category_name)}",
            "name": category_name,
            "color": category_color,
            "description": f"{category_name} includes: {subcategory_names}.",
            "monthly_limit": monthly_limit,
            "subcategories": subcategories
        })

    internal_transfer_keywords = split_comma_input(
        request.form.get("internal_transfer_keywords", "")
    )

    own_account_transfer_keywords = split_comma_input(
        request.form.get("own_account_transfer_keywords", "")
    )

    salary_keywords = split_comma_input(
        request.form.get("salary_keywords", "")
    )

    ignore_keywords = split_comma_input(
        request.form.get("ignore_keywords", "")
    )

    if not internal_transfer_keywords:
        internal_transfer_keywords = ["expenses", "transfer", "tpp"]

    if not salary_keywords:
        salary_keywords = ["salary", "payroll", "wages"]

    if not ignore_keywords:
        ignore_keywords = ["balance forward"]

    config = {
        "budget_period": {
            "start_date": start_date,
            "end_date": end_date
        },
        "accounts": accounts,
        "categories": categories,
        "rules": {
            "internal_transfer_keywords": internal_transfer_keywords,
            "own_account_transfer_keywords": own_account_transfer_keywords,
            "salary_keywords": salary_keywords,
            "ignore_keywords": ignore_keywords
        },
        "transfer_matching": {
            "amount_tolerance": "0.00",
            "description_similarity_threshold": 0.50
        },
        "planned_savings": planned_savings
    }

    return config


def get_review_transactions(processed):
    return [
        tx for tx in processed
        if tx.get("needs_review") == "yes"
        or tx.get("transaction_type") == "external_transfer_out"
        or tx.get("categorization_method") == "ai_embedding"
    ]


def find_category_and_subcategory(config, category_name, subcategory_name):
    for category in config.get("categories", []):
        if category.get("name", "").lower() == str(category_name).lower():
            for subcategory in category.get("subcategories", []):
                if subcategory.get("name", "").lower() == str(subcategory_name).lower():
                    return {
                        "category_id": category.get("category_id", ""),
                        "category_name": category.get("name", ""),
                        "subcategory": subcategory.get("name", "")
                    }

    return None


def build_merchant_pattern(description):
    text = str(description or "").lower()

    remove_parts = [
        "vdc-",
        "vdp-",
        "posc",
        "pos",
        "d/d",
        "www.aib.ie/standardconditions",
        "allied irish banks",
        "p.l.c.",
        "regulated"
    ]

    for part in remove_parts:
        text = text.replace(part, " ")

    text = re.sub(r"\b\d{1,2}(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    words = [
        word for word in text.split()
        if len(word) >= 3
    ]

    if not words:
        return text.strip()

    return " ".join(words[:3])


def append_learned_rule_if_needed(rules, new_rule):
    merchant_pattern = str(new_rule.get("merchant_pattern", "")).lower().strip()
    category_name = str(new_rule.get("category_name", "")).lower().strip()
    subcategory = str(new_rule.get("subcategory", "")).lower().strip()

    if not merchant_pattern or not category_name or not subcategory:
        return rules

    for rule in rules:
        same_merchant = str(rule.get("merchant_pattern", "")).lower().strip() == merchant_pattern
        same_category = str(rule.get("category_name", "")).lower().strip() == category_name
        same_subcategory = str(rule.get("subcategory", "")).lower().strip() == subcategory

        if same_merchant and same_category and same_subcategory:
            return rules

    rules.append(new_rule)
    return rules


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/process", methods=["POST"])
def process_budget():
    ensure_learned_rules_file()

    run_id = str(uuid.uuid4())
    temp_run_dir = TEMP_RUNS_DIR / run_id
    review_run_dir = REVIEW_RUNS_DIR / run_id

    temp_run_dir.mkdir(parents=True, exist_ok=True)
    review_run_dir.mkdir(parents=True, exist_ok=True)

    try:
        config = build_config_from_form(temp_run_dir)

        start_date = config["budget_period"]["start_date"]
        end_date = config["budget_period"]["end_date"]

        all_transactions = load_all_transactions(config)

        period_transactions = filter_budget_period(
            all_transactions,
            start_date,
            end_date
        )

        mark_transfer_matches(period_transactions, config)

        processed = process_transactions(period_transactions, config)

        review_transactions = get_review_transactions(processed)

        review_state = {
            "run_id": run_id,
            "config": config,
            "processed": processed,
            "transaction_count": len(period_transactions)
        }

        save_json_file(review_run_dir / "review_state.json", review_state)

        return render_template(
            "review.html",
            run_id=run_id,
            config=config,
            transactions=review_transactions,
            transaction_count=len(period_transactions),
            review_count=len(review_transactions)
        )

    except Exception as error:
        flash(str(error))
        return redirect(url_for("index"))

    finally:
        if temp_run_dir.exists():
            shutil.rmtree(temp_run_dir, ignore_errors=True)


@app.route("/finalize/<run_id>", methods=["POST"])
def finalize_budget(run_id):
    ensure_learned_rules_file()

    safe_run_id = secure_filename(run_id)
    review_run_dir = REVIEW_RUNS_DIR / safe_run_id
    review_state_path = review_run_dir / "review_state.json"

    if not review_state_path.exists():
        flash("Review session expired. Please generate the report again.")
        return redirect(url_for("index"))

    state = load_json_file(review_state_path, {})
    config = state["config"]
    processed = state["processed"]

    learned_rules = load_json_file(LEARNED_RULES_FILE, [])

    for tx in processed:
        tx_id = tx.get("transaction_id")

        if not tx_id:
            continue

        category_value = request.form.get(f"category_choice_{tx_id}", "")
        include_transfer_value = request.form.get(f"include_transfer_{tx_id}", "")

        if tx.get("transaction_type") == "external_transfer_out":
            if include_transfer_value == "yes":
                tx["include_in_expenses"] = "yes"
                tx["review_decision"] = "user_included_transfer_as_expense"
            elif include_transfer_value == "no":
                tx["include_in_expenses"] = "no"
                tx["review_decision"] = "user_excluded_transfer_from_expense"
            else:
                tx["include_in_expenses"] = "no"
                tx["review_decision"] = "user_left_transfer_unresolved"

        if category_value and category_value != "__keep__":
            if "||" in category_value:
                category_name, subcategory_name = category_value.split("||", 1)

                category_match = find_category_and_subcategory(
                    config,
                    category_name,
                    subcategory_name
                )

                if category_match:
                    old_category = tx.get("category_name", "")
                    old_subcategory = tx.get("subcategory", "")

                    tx["category_id"] = category_match["category_id"]
                    tx["category_name"] = category_match["category_name"]
                    tx["subcategory"] = category_match["subcategory"]
                    tx["categorization_method"] = "user_review"
                    tx["needs_review"] = "no"
                    tx["review_decision"] = "user_confirmed_or_corrected_category"

                    if (
                        old_category.lower() != tx["category_name"].lower()
                        or old_subcategory.lower() != tx["subcategory"].lower()
                    ):
                        merchant_pattern = build_merchant_pattern(tx.get("description", ""))

                        learned_rules = append_learned_rule_if_needed(
                            learned_rules,
                            {
                                "merchant_pattern": merchant_pattern,
                                "category_id": tx["category_id"],
                                "category_name": tx["category_name"],
                                "subcategory": tx["subcategory"]
                            }
                        )

        if category_value == "__keep__" and tx.get("categorization_method") == "ai_embedding":
            tx["needs_review"] = "no"
            tx["review_decision"] = "user_kept_ai_category"

        if tx.get("transaction_type") == "external_transfer_out" and include_transfer_value in ["yes", "no"]:
            if tx.get("category_name") == "Transfer to Other Account":
                tx["needs_review"] = "no"

    save_json_file(LEARNED_RULES_FILE, learned_rules)

    start_date = config["budget_period"]["start_date"]
    end_date = config["budget_period"]["end_date"]

    summary = create_summary(
        processed,
        config,
        start_date,
        end_date
    )

    excel_filename = f"budget_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_run_id[:8]}.xlsx"
    excel_path = DOWNLOADS_DIR / excel_filename

    create_excel_report(
        processed_transactions=processed,
        summary=summary,
        config=config,
        output_path=excel_path,
        learned_rules_path=LEARNED_RULES_FILE
    )

    temp_csv_path = review_run_dir / OUTPUT_CSV_NAME
    temp_summary_path = review_run_dir / OUTPUT_SUMMARY_NAME

    save_processed_csv(processed, temp_csv_path)
    save_json(summary, temp_summary_path)

    if review_run_dir.exists():
        shutil.rmtree(review_run_dir, ignore_errors=True)

    download_url = url_for("download_report", filename=excel_filename)

    return render_template(
        "result.html",
        summary=summary,
        download_url=download_url,
        transaction_count=state.get("transaction_count", len(processed)),
        review_count=summary.get("review_count", 0),
        uncategorized_count=summary.get("uncategorized_count", 0)
    )


@app.route("/download/<filename>", methods=["GET"])
def download_report(filename):
    safe_filename = secure_filename(filename)
    file_path = DOWNLOADS_DIR / safe_filename

    if not file_path.exists():
        flash("Report file no longer exists. Please generate the report again.")
        return redirect(url_for("index"))

    @after_this_request
    def delete_file_after_download(response):
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

        return response

    return send_file(
        file_path,
        as_attachment=True,
        download_name="budget_report.xlsx"
    )


if __name__ == "__main__":
    app.run(debug=True)