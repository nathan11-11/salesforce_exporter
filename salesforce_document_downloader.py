import json
import os
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

DOWNLOAD_FOLDER = "Downloads"
QUERY_FILE = "query.soql"


def xml_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def load_config():
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_query():
    with open(QUERY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def login(config):
    api = config.get("api_version", "63.0").lstrip("v")
    login_url = config["login_url"].rstrip("/")
    password = config["password"] + config["security_token"]

    body = f"""<?xml version="1.0"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
    <env:Body>
        <n1:login xmlns:n1="urn:partner.soap.sforce.com">
            <n1:username>{xml_escape(config["username"])}</n1:username>
            <n1:password>{xml_escape(password)}</n1:password>
        </n1:login>
    </env:Body>
</env:Envelope>"""

    print("Connecting to Salesforce...")

    response = requests.post(
        f"{login_url}/services/Soap/u/{api}",
        data=body.encode(),
        headers={
            "Content-Type": "text/xml; charset=UTF-8",
            "SOAPAction": "login",
        },
    )

    response.raise_for_status()

    root = ET.fromstring(response.text)
    ns = {"sf": "urn:partner.soap.sforce.com"}

    session_id = root.findtext(".//sf:sessionId", namespaces=ns)
    server_url = root.findtext(".//sf:serverUrl", namespaces=ns)

    instance = server_url.split("/services/Soap/")[0]

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {session_id}"

    print(f"Connected to {instance}")

    return session, instance, api


def execute_query(session, instance, api, soql):
    encoded_query = quote(soql, safe="")
    url = f"{instance}/services/data/v{api}/query/?q={encoded_query}"

    records = []

    while url:
        response = session.get(url)
        response.raise_for_status()

        result = response.json()

        records.extend(result.get("records", []))

        next_records = result.get("nextRecordsUrl")

        if next_records:
            url = instance + next_records
        else:
            url = None

    return records


def sanitize_filename(name):
    invalid = '<>:"/\\|?*'
    for char in invalid:
        name = name.replace(char, "_")
    return name.strip()


def download_documents(session, instance, records):
    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

    print(f"Found {len(records)} document(s).")

    for i, record in enumerate(records, start=1):

        content_doc = record.get("ContentDocument", {})

        title = content_doc.get("Title", f"Document_{i}")
        title = sanitize_filename(title)

        extension = record.get("FileExtension", "bin")

        download_url = instance + record["VersionData"]

        filename = f"{title}.{extension}"
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)

        print(f"Downloading {filename}")

        response = session.get(download_url)
        response.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(response.content)

    print("Download complete.")


def main():
    config = load_config()
    soql = load_query()

    session, instance, api = login(config)

    records = execute_query(session, instance, api, soql)

    download_documents(session, instance, records)


if __name__ == "__main__":
    main()