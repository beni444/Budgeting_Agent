# Budget Agent

**Turn raw bank statement PDFs into a reviewed, categorized, savings-aware budget report — automatically.**

Budget Agent is a Flask-based personal budgeting app that parses PDF bank statements, extracts transactions, categorizes spending using a hybrid rules + AI engine, filters out internal transfers, and produces a polished Excel report comparing actual spend against your planned budget.

---

## What it does

Upload a PDF bank statement → get back a categorized, transfer-aware Excel budget report, with your corrections learned for next time.

- **Parses PDF bank statements** — no manual CSV exports or copy-pasting transactions
- **Categorizes spending automatically** — combining deterministic rules with AI-assisted matching
- **Detects internal transfers** — so moving money between your own accounts doesn't get double-counted as spending
- **Learns from you** — every correction becomes a permanent rule, so accuracy improves every month
- **Outputs a real Excel report** — category summaries, limits, transaction detail, and savings analysis, not just a dashboard you can't take with you

---

## Architecture

The app is split into independent stages, each owning one part of the pipeline:

```
PDF Statement
      │
      ▼
┌─────────────────────────┐
│  pdf_bank_statement_     │  pdfplumber + regex + layout-aware
│  parser.py                │  table extraction → structured
│                           │  transactions (date, description,
│                           │  debit/credit, balance) + validation
└────────────┬─────────────┘
             ▼
┌─────────────────────────┐
│  ai_categorizer.py        │  learned merchant rules → keyword
│                           │  matching → semantic embeddings,
│                           │  in that priority order. Low-confidence
│                           │  matches are flagged for review.
└────────────┬─────────────┘
             ▼
┌─────────────────────────┐
│  budget_engine.py          │  transfer detection (date + amount +
│                           │  account + reference matching),
│                           │  external transfer review, planned
│                           │  vs. actual comparison, savings calc
└────────────┬─────────────┘
             ▼
┌─────────────────────────┐
│  excel_report.py           │  color-coded, multi-sheet Excel
│                           │  workbook: summary, categories,
│                           │  transactions, transfers, review items
└─────────────────────────┘
```

`app.py` wires these stages into a Flask workflow (upload → setup → review → download), and `setup_config.py` / `budget_config.json` hold the configurable budgeting model — accounts, income source, categories, subcategories, limits, and savings targets. `learned_rules.json` persists every correction you make as a merchant-matching rule.

### Design choices worth knowing

- **Deterministic before AI** — learned rules and exact matches always win over model predictions. The categorizer only reaches for semantic matching when nothing more certain applies, and anything uncertain or high-impact gets routed to you instead of guessed.
- **Human-in-the-loop learning** — corrections aren't thrown away after one run; they become persistent rules, so the system gets more accurate the more you use it.
- **Privacy by design** — uploaded statements and extracted transaction history are processed for the current run only and deleted after the report is generated. Nothing is stored long-term.

---

## Tech stack

| Layer | Tools |
|---|---|
| Backend | Flask |
| PDF parsing | pdfplumber, regex-based layout extraction |
| Data processing | pandas |
| Categorization | rule-based matching + sentence-transformers (semantic embeddings), scikit-learn |
| Reporting | openpyxl / xlsxwriter |
| Frontend | Flask templates, HTML/CSS |

---

## Project structure

```
Budgeting_Agent/
├── app.py                          # Flask app: upload, setup, review, download
├── pdf_bank_statement_parser.py    # PDF → structured transactions
├── ai_categorizer.py               # Rule-based + AI categorization engine
├── budget_engine.py                # Transfer detection, budget comparison, savings
├── excel_report.py                 # Excel report generation
├── setup_config.py                 # Budget model configuration logic
├── budget_config.json              # User-defined accounts/categories/limits
├── learned_rules.json              # Persisted merchant categorization rules
├── templates/                      # Flask HTML templates
├── static/                         # CSS/JS assets
└── requirements.txt
```

---

## Getting started

```bash
git clone https://github.com/beni444/Budgeting_Agent.git
cd Budgeting_Agent
pip install -r requirements.txt
python app.py
```

Then open the app in your browser, upload a bank statement PDF, set up your budget (accounts, categories, limits, savings targets), review the categorized transactions, and download your Excel report.

> **Note:** first run may take a moment to download the sentence-transformers embedding model.

---

## Roadmap ideas

- Multi-statement / multi-month trend reporting
- Support for additional bank statement formats
- Configurable category-limit alerts

---

## Privacy

Bank statements and extracted transactions are processed in-memory / temporarily for the duration of a single run and deleted once the report is generated. No transaction data is persisted beyond your local `learned_rules.json` and `budget_config.json`.

