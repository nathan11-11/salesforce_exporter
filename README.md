# Salesforce File Exporter

A small, production-minded Python CLI for exporting Salesforce Files selected by a SOQL query. It logs in through the Partner SOAP API, paginates a REST query, and streams each `ContentVersion` binary to disk.

> **Portfolio-safe example:** this repository is a sanitized reconstruction of an internal automation. It intentionally contains no production credentials, proprietary object names, business rules, or data.

## Why this exists

Teams often need a reproducible way to retrieve Salesforce attachments for review, migration, or archival. This tool keeps the selection logic in a version-controlled SOQL file and handles the repetitive API and file-system work locally.

## Highlights

- Uses SOAP authentication and REST API queries in one focused workflow.
- Follows Salesforce pagination links, so queries are not limited to the first response page.
- Streams file content to disk instead of holding downloads in memory.
- Retries transient HTTP failures with exponential backoff and uses request timeouts.
- Validates configuration before authenticating and never prints credentials or session tokens.
- Produces Windows-safe names, prevents accidental overwrites, and supports a `--dry-run` preview.
- Includes unit tests and a GitHub Actions quality check.

## Quick start

Requires Python 3.10 or newer.

```bash
git clone <your-repository-url>
cd salesforce_exporter
python -m venv .venv
```

Activate the environment, then install the dependency:

```bash
# PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a private local configuration:

```bash
Copy-Item config.example.json config.json
```

Fill in `config.json`, then review `query.soql` and run a safe preview:

```bash
python salesforce_document_downloader.py --dry-run
```

When the results look right, download them:

```bash
python salesforce_document_downloader.py --output Downloads
```

`config.json` and `Downloads/` are ignored by Git.

## Configuration

`config.json` is deliberately excluded from source control. It needs the following values:

```json
{
  "login_url": "https://your-domain.my.salesforce.com",
  "api_version": "63.0",
  "username": "your.email@example.com",
  "password": "YOUR_PASSWORD",
  "security_token": "YOUR_SECURITY_TOKEN"
}
```

Use a dedicated Salesforce integration user with only the access required for the chosen query. Salesforce appends the security token to the password for this login flow.

## Query requirements

The query can be tailored to the Salesforce environment, but each returned record must include:

- `VersionData`
- `FileExtension`

Including `ContentDocument.Title` is recommended to create readable local names. The included query is a generic, limited `ContentVersion` example.

## Command-line options

```text
python salesforce_document_downloader.py [-h] [--config CONFIG] [--query QUERY]
                                         [--output OUTPUT] [--timeout TIMEOUT]
                                         [--overwrite] [--dry-run] [--verbose]
```

- `--dry-run` lists the files the query would download without creating a folder or writing data.
- `--overwrite` replaces matching filenames. By default, the exporter appends ` (2)`, ` (3)`, and so on.
- `--verbose` enables more detailed request diagnostics. Do not share verbose logs if they contain organization-specific endpoints.

## Quality checks

Install the optional development dependency and run the same checks used in CI:

```bash
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests -v
```

## Repository layout

```text
salesforce_exporter/
├── salesforce_document_downloader.py  # CLI and API workflow
├── query.soql                         # Editable selection criteria
├── config.example.json                # Safe configuration template
├── tests/                             # Network-free unit tests
└── .github/workflows/ci.yml           # Lint and test automation
```
