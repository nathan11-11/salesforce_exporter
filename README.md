# Salesforce Document Downloader

A Python utility that authenticates with Salesforce using the SOAP API, executes a configurable SOQL query, and downloads Salesforce Files locally using the REST API.

## Features

- SOAP authentication
- REST API integration
- Configurable SOQL queries
- Automatic file downloads
- Pagination support for large query results
- Downloads organized into a local folder

## Requirements

- Python 3.10+
- requests

Install dependencies:

```bash
pip install requests
```

## Configuration

Copy:

```
config.example.json
```

to

```
config.json
```

Then populate your Salesforce credentials.

Example:

```json
{
    "login_url": "https://your-company.my.salesforce.com",
    "api_version": "63.0",
    "username": "your.email@example.com",
    "password": "YOUR_PASSWORD",
    "security_token": "YOUR_SECURITY_TOKEN"
}
```

## Running

```bash
python salesforce_document_downloader.py
```

## Notes

This repository contains a generic example implementation. The original production version was developed for an enterprise Salesforce environment and has been sanitized to remove organization-specific objects, field names, business logic, and credentials.