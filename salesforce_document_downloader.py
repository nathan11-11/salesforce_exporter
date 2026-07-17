"""Export Salesforce Files selected by a SOQL query.

The tool authenticates with Salesforce's Partner SOAP API, runs a REST SOQL
query, and saves the returned ContentVersion binaries to a local folder.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_API_VERSION = "63.0"
DEFAULT_TIMEOUT_SECONDS = 30
CHUNK_SIZE_BYTES = 64 * 1024
INVALID_FILENAME_CHARACTERS = '<>:"/\\|?*'
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

LOGGER = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when the configuration file cannot be used safely."""


class SalesforceResponseError(RuntimeError):
    """Raised when Salesforce responds without the information we need."""


@dataclass(frozen=True)
class SalesforceConfig:
    """Credentials and endpoint required for a Salesforce SOAP login."""

    login_url: str
    api_version: str
    username: str
    password: str
    security_token: str

    @classmethod
    def from_mapping(cls, raw_config: Mapping[str, Any]) -> SalesforceConfig:
        required_keys = ("login_url", "username", "password", "security_token")
        missing_keys = [key for key in required_keys if not str(raw_config.get(key, "")).strip()]
        if missing_keys:
            joined_keys = ", ".join(missing_keys)
            raise ConfigurationError(f"config.json is missing a value for: {joined_keys}")

        login_url = str(raw_config["login_url"]).rstrip("/")
        if not login_url.startswith("https://"):
            raise ConfigurationError("login_url must start with https://")

        api_version = str(raw_config.get("api_version", DEFAULT_API_VERSION)).lstrip("v")
        if not re.fullmatch(r"\d+\.\d+", api_version):
            raise ConfigurationError("api_version must be in the form 63.0")

        return cls(
            login_url=login_url,
            api_version=api_version,
            username=str(raw_config["username"]),
            password=str(raw_config["password"]),
            security_token=str(raw_config["security_token"]),
        )


def xml_escape(value: object) -> str:
    """Escape text interpolated into the SOAP XML payload."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def load_config(config_path: Path) -> SalesforceConfig:
    """Load and validate a JSON configuration file."""
    try:
        with config_path.open("r", encoding="utf-8") as config_file:
            raw_config = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{config_path} is not valid JSON: {error.msg}") from error

    if not isinstance(raw_config, dict):
        raise ConfigurationError(f"{config_path} must contain a JSON object")

    return SalesforceConfig.from_mapping(raw_config)


def load_query(query_path: Path) -> str:
    """Read a SOQL query, rejecting an empty file before contacting Salesforce."""
    query = query_path.read_text(encoding="utf-8").strip()
    if not query:
        raise ConfigurationError(f"{query_path} is empty")
    return query


def create_session() -> requests.Session:
    """Create an HTTP session with conservative retries for transient failures."""
    retry_policy = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry_policy)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def login(
    config: SalesforceConfig, session: requests.Session, timeout: float
) -> tuple[str, str]:
    """Authenticate with the Partner SOAP API and configure a REST bearer token."""
    password_with_token = f"{config.password}{config.security_token}"
    body = f'''<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
    <env:Body>
        <n1:login xmlns:n1="urn:partner.soap.sforce.com">
            <n1:username>{xml_escape(config.username)}</n1:username>
            <n1:password>{xml_escape(password_with_token)}</n1:password>
        </n1:login>
    </env:Body>
</env:Envelope>'''

    LOGGER.info("Authenticating with Salesforce")
    response = session.post(
        f"{config.login_url}/services/Soap/u/{config.api_version}",
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": "login"},
        timeout=timeout,
    )
    response.raise_for_status()

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as error:
        raise SalesforceResponseError("Salesforce returned an invalid SOAP response") from error

    namespace = {"sf": "urn:partner.soap.sforce.com"}
    session_id = root.findtext(".//sf:sessionId", namespaces=namespace)
    server_url = root.findtext(".//sf:serverUrl", namespaces=namespace)
    if not session_id or not server_url or "/services/Soap/" not in server_url:
        raise SalesforceResponseError(
            "Salesforce login response did not include a session endpoint"
        )

    instance_url = server_url.split("/services/Soap/", maxsplit=1)[0]
    session.headers["Authorization"] = f"Bearer {session_id}"
    LOGGER.info("Authenticated with Salesforce instance %s", instance_url)
    return instance_url, config.api_version


def execute_query(
    session: requests.Session, instance_url: str, api_version: str, soql: str, timeout: float
) -> list[dict[str, Any]]:
    """Execute SOQL and follow Salesforce pagination links until all records are read."""
    url: str | None = f"{instance_url}/services/data/v{api_version}/query/"
    query_parameters: dict[str, str] | None = {"q": soql}
    records: list[dict[str, Any]] = []

    while url:
        response = session.get(url, params=query_parameters, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        page_records = result.get("records", [])
        if not isinstance(page_records, list):
            raise SalesforceResponseError(
                "Salesforce query response contains an invalid records value"
            )

        records.extend(page_records)
        next_records_url = result.get("nextRecordsUrl")
        url = urljoin(f"{instance_url}/", next_records_url) if next_records_url else None
        query_parameters = None

    return records


def sanitize_filename(name: object) -> str:
    """Return a portable filename stem suitable for Windows and macOS."""
    cleaned = str(name).strip()
    for character in INVALID_FILENAME_CHARACTERS:
        cleaned = cleaned.replace(character, "_")
    cleaned = cleaned.rstrip(". ") or "untitled"
    if cleaned.upper() in WINDOWS_RESERVED_FILENAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:180]


def document_filename(record: Mapping[str, Any], position: int) -> str:
    """Build a safe, meaningful filename from a ContentVersion query record."""
    content_document = record.get("ContentDocument") or {}
    if not isinstance(content_document, Mapping):
        content_document = {}

    title = sanitize_filename(content_document.get("Title", f"Document_{position}"))
    extension = sanitize_filename(record.get("FileExtension", "bin")).lstrip(".") or "bin"
    return f"{title}.{extension}"


def available_path(path: Path, overwrite: bool) -> Path:
    """Avoid overwriting an existing file unless the caller explicitly permits it."""
    if overwrite or not path.exists():
        return path

    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise SalesforceResponseError(f"Could not find a unique filename for {path.name}")


def download_documents(
    session: requests.Session,
    instance_url: str,
    records: Sequence[Mapping[str, Any]],
    output_directory: Path,
    timeout: float,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    """Stream Salesforce content versions to disk and return the saved-file count."""
    if not dry_run:
        output_directory.mkdir(parents=True, exist_ok=True)

    LOGGER.info("Found %d document(s)", len(records))
    downloaded_count = 0

    for position, record in enumerate(records, start=1):
        version_data_path = record.get("VersionData")
        if not isinstance(version_data_path, str) or not version_data_path:
            LOGGER.warning("Skipping record %d because it has no VersionData path", position)
            continue

        destination = available_path(
            output_directory / document_filename(record, position), overwrite
        )
        if dry_run:
            LOGGER.info("Would download %s", destination.name)
            continue

        LOGGER.info("Downloading %s", destination.name)
        response = session.get(
            urljoin(f"{instance_url}/", version_data_path), stream=True, timeout=timeout
        )
        response.raise_for_status()
        with destination.open("wb") as output_file:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE_BYTES):
                if chunk:
                    output_file.write(chunk)
        downloaded_count += 1

    return downloaded_count


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface."""
    parser = argparse.ArgumentParser(
        description="Download Salesforce Files returned by a SOQL query."
    )
    parser.add_argument(
        "--config", type=Path, default=Path("config.json"), help="JSON credentials file"
    )
    parser.add_argument("--query", type=Path, default=Path("query.soql"), help="SOQL query file")
    parser.add_argument(
        "--output", type=Path, default=Path("Downloads"), help="Folder for downloaded files"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace files with matching names"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List matching files without downloading them"
    )
    parser.add_argument("--verbose", action="store_true", help="Show diagnostic logging")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the exporter and return an application exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.timeout <= 0:
        LOGGER.error("--timeout must be greater than zero")
        return 2

    try:
        config = load_config(args.config)
        soql = load_query(args.query)
        session = create_session()
        instance_url, api_version = login(config, session, args.timeout)
        records = execute_query(session, instance_url, api_version, soql, args.timeout)
        downloaded_count = download_documents(
            session,
            instance_url,
            records,
            args.output,
            args.timeout,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
        )
    except (
        ConfigurationError,
        FileNotFoundError,
        OSError,
        requests.RequestException,
        SalesforceResponseError,
    ) as error:
        LOGGER.error("Export failed: %s", error)
        return 1

    if args.dry_run:
        LOGGER.info("Dry run complete")
    else:
        LOGGER.info("Download complete: %d file(s) saved to %s", downloaded_count, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
