import json
from pathlib import Path


OUTPUT_CONFIG_FILE = "budget_config.json"


DEFAULT_CATEGORIES = [
    {
        "name": "Essentials",
        "color": "red",
        "subcategories": [
            {
                "name": "Rent",
                "description": "Monthly rent or housing payments."
            },
            {
                "name": "Groceries",
                "description": "Food shopping and supermarket purchases."
            },
            {
                "name": "Bills",
                "description": "Utilities, internet, phone, electricity, gas, and recurring household bills."
            }
        ]
    },
    {
        "name": "Optionals",
        "color": "yellow",
        "subcategories": [
            {
                "name": "Shopping",
                "description": "Clothes, jewellery, beauty, gifts, online shopping, and non-essential personal purchases."
            },
            {
                "name": "Investments",
                "description": "Money put into investments, trading accounts, savings pots, or wealth-building accounts."
            }
        ]
    },
    {
        "name": "Extras",
        "color": "green",
        "subcategories": [
            {
                "name": "EMI",
                "description": "Monthly EMI, loan repayments, instalments, or financed purchases."
            },
            {
                "name": "Eating Out",
                "description": "Restaurants, cafes, takeaways, coffee, and casual food spending."
            }
        ]
    }
]


def ask_required(prompt):
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("This is required.")


def ask_optional(prompt, default=""):
    value = input(prompt).strip()
    return value if value else default


def ask_yes_no(prompt, default=None):
    while True:
        suffix = " (y/n): "

        if default is True:
            suffix = " (Y/n): "
        elif default is False:
            suffix = " (y/N): "

        value = input(prompt + suffix).strip().lower()

        if not value and default is not None:
            return default

        if value in ["y", "yes"]:
            return True

        if value in ["n", "no"]:
            return False

        print("Please enter y or n.")


def ask_int(prompt, default=None, min_value=1):
    while True:
        default_text = f" [{default}]" if default is not None else ""
        value = input(prompt + default_text + ": ").strip()

        if not value and default is not None:
            return default

        try:
            number = int(value)
            if number >= min_value:
                return number
        except ValueError:
            pass

        print(f"Please enter a whole number greater than or equal to {min_value}.")


def ask_number(prompt, default=None):
    while True:
        default_text = f" [{default}]" if default is not None else ""
        value = input(prompt + default_text + ": ").strip()

        if not value and default is not None:
            return float(default)

        try:
            return float(value)
        except ValueError:
            print("Please enter a valid number.")


def ask_comma_list(prompt):
    value = input(prompt).strip()

    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def slugify(value):
    return (
        value.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
    )


def create_account_config():
    accounts = []

    print("\n==============================")
    print("ACCOUNT SETUP")
    print("==============================")

    number_of_accounts = ask_int(
        "How many bank accounts do you want to use for this budget?",
        default=2
    )

    print("\nFor each account, enter a nickname and the JSON file created by your PDF parser.")
    print("Example JSON file: aib_transactions.json\n")

    for index in range(1, number_of_accounts + 1):
        print(f"Account {index} of {number_of_accounts}")

        display_name = ask_required(
            "Account nickname, example: AIB Main Salary Account: "
        )

        transaction_file = ask_required(
            "Transaction JSON file for this account: "
        )

        receives_income = ask_yes_no(
            "Do you receive salary/income in this account?",
            default=(index == 1)
        )

        accounts.append({
            "account_id": f"account_{index}_{slugify(display_name)}",
            "display_name": display_name,
            "is_main_income_account": receives_income,
            "transaction_file": transaction_file
        })

        print()

    return accounts


def create_categories_from_defaults():
    categories = []

    print("\nUsing default 3-category setup:")
    print("1. Red - Essentials")
    print("2. Yellow - Optionals")
    print("3. Green - Extras")

    for index, default_category in enumerate(DEFAULT_CATEGORIES, start=1):
        category_name = ask_optional(
            f"\nCategory {index} name [{default_category['name']}]: ",
            default_category["name"]
        )

        category_color = ask_optional(
            f"Category {index} color [{default_category['color']}]: ",
            default_category["color"]
        )

        monthly_limit = ask_number(
            f"Monthly spending limit for {category_name}",
            default=0
        )

        use_default_subcategories = ask_yes_no(
            f"Use suggested subcategories for {category_name}?",
            default=True
        )

        subcategories = []

        if use_default_subcategories:
            for sub_index, sub in enumerate(default_category["subcategories"], start=1):
                subcategories.append({
                    "subcategory_id": f"cat_{index}_sub_{sub_index}_{slugify(sub['name'])}",
                    "name": sub["name"],
                    "description": sub["description"],
                    "examples": [],
                    "keywords": []
                })

        else:
            subcategories = ask_subcategories(category_index=index, category_name=category_name)

        categories.append({
            "category_id": f"cat_{index}_{slugify(category_name)}",
            "name": category_name,
            "color": category_color,
            "description": f"{category_color} category for {category_name}.",
            "monthly_limit": monthly_limit,
            "subcategories": subcategories
        })

    return categories


def ask_subcategories(category_index, category_name):
    subcategories = []

    number_of_subcategories = ask_int(
        f"How many subcategories do you want under {category_name}?",
        default=3
    )

    print("\nEnter each subcategory as:")
    print("name - short description")
    print("Example: Groceries - supermarket and food shopping\n")

    for sub_index in range(1, number_of_subcategories + 1):
        raw = ask_required(
            f"Subcategory {sub_index}: "
        )

        if "-" in raw:
            name, description = raw.split("-", 1)
            name = name.strip()
            description = description.strip()
        else:
            name = raw.strip()
            description = ask_required(
                f"Short description for {name}: "
            )

        subcategories.append({
            "subcategory_id": f"cat_{category_index}_sub_{sub_index}_{slugify(name)}",
            "name": name,
            "description": description,
            "examples": [],
            "keywords": []
        })

    return subcategories


def create_custom_categories():
    categories = []

    number_of_categories = ask_int(
        "How many categories do you want in the budget?",
        default=3
    )

    for category_index in range(1, number_of_categories + 1):
        print(f"\nCategory {category_index} of {number_of_categories}")

        category_name = ask_required(
            "Category name, example: Essentials: "
        )

        category_color = ask_required(
            "Category color, example: red/yellow/green/blue: "
        )

        monthly_limit = ask_number(
            f"Monthly spending limit for {category_name}",
            default=0
        )

        subcategories = ask_subcategories(
            category_index=category_index,
            category_name=category_name
        )

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

    return categories


def create_category_config():
    print("\n==============================")
    print("CATEGORY SETUP")
    print("==============================")

    use_defaults = ask_yes_no(
        "Do you want to start with the default 3 categories: Red/Essentials, Yellow/Optionals, Green/Extras?",
        default=True
    )

    if use_defaults:
        return create_categories_from_defaults()

    return create_custom_categories()


def create_rules_config(accounts):
    print("\n==============================")
    print("TRANSFER RULE SETUP")
    print("==============================")

    print("These rules help avoid double-counting money moved between your own accounts.")
    print("You can press Enter to accept the defaults.\n")

    account_names = []
    for account in accounts:
        account_names.append(account["display_name"])

    default_internal_keywords = [
        "expenses",
        "revolut",
        "transfer",
        "*tpp",
        "tpp"
    ]

    default_own_keywords = account_names

    internal_transfer_keywords = ask_comma_list(
        "Internal transfer keywords, comma-separated [expenses, revolut, transfer, *tpp, tpp]: "
    )

    if not internal_transfer_keywords:
        internal_transfer_keywords = default_internal_keywords

    own_account_transfer_keywords = ask_comma_list(
        "Own account/name keywords, comma-separated. Example: your name, Revolut account name. Press Enter to skip: "
    )

    if not own_account_transfer_keywords:
        own_account_transfer_keywords = default_own_keywords

    salary_keywords = ask_comma_list(
        "Salary/income keywords, comma-separated [salary, payroll, wages]: "
    )

    if not salary_keywords:
        salary_keywords = ["salary", "payroll", "wages"]

    ignore_keywords = ask_comma_list(
        "Ignore keywords, comma-separated [balance forward]: "
    )

    if not ignore_keywords:
        ignore_keywords = ["balance forward"]

    return {
        "internal_transfer_keywords": internal_transfer_keywords,
        "own_account_transfer_keywords": own_account_transfer_keywords,
        "salary_keywords": salary_keywords,
        "ignore_keywords": ignore_keywords
    }


def create_budget_config():
    print("\n========================================")
    print("BUDGET SETUP AGENT")
    print("========================================")

    print("\nThis setup is intentionally short.")
    print("You only define accounts, categories, subcategories, and transfer clues.")
    print("The AI categorizer can infer merchants later and learn from corrections.\n")

    start_date = ask_required(
        "Budget start date, example 2025-10-01: "
    )

    end_date = ask_required(
        "Budget end date, example 2025-10-31: "
    )

    planned_savings = ask_number(
        "How much do you plan to save in this budget period?",
        default=0
    )

    accounts = create_account_config()
    categories = create_category_config()
    rules = create_rules_config(accounts)

    config = {
        "budget_period": {
            "start_date": start_date,
            "end_date": end_date
        },
        "accounts": accounts,
        "categories": categories,
        "rules": rules,
        "planned_savings": planned_savings
    }

    return config


def save_config(config):
    with open(OUTPUT_CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2, ensure_ascii=False)

    print("\n========================================")
    print("CONFIG CREATED")
    print("========================================")
    print(f"Saved to: {OUTPUT_CONFIG_FILE}")


def main():
    if Path(OUTPUT_CONFIG_FILE).exists():
        overwrite = ask_yes_no(
            f"{OUTPUT_CONFIG_FILE} already exists. Do you want to overwrite it?",
            default=False
        )

        if not overwrite:
            print("Cancelled. Existing config was not changed.")
            return

    config = create_budget_config()
    save_config(config)


if __name__ == "__main__":
    main()