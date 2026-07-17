import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import salesforce_document_downloader as downloader


class FilenameTests(unittest.TestCase):
    def test_sanitize_filename_replaces_invalid_characters_and_reserved_name(self):
        self.assertEqual(downloader.sanitize_filename('name<>:"/\\|?* '), "name_________")
        self.assertEqual(downloader.sanitize_filename("CON"), "_CON")

    def test_document_filename_uses_a_safe_fallback(self):
        filename = downloader.document_filename({"FileExtension": "pdf"}, 3)
        self.assertEqual(filename, "Document_3.pdf")

    def test_available_path_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "report.pdf"
            destination.write_text("existing", encoding="utf-8")
            self.assertEqual(
                downloader.available_path(destination, overwrite=False).name,
                "report (2).pdf",
            )


class QueryTests(unittest.TestCase):
    def test_execute_query_follows_the_next_records_url(self):
        first_response = Mock()
        first_response.json.return_value = {
            "records": [{"Id": "first"}],
            "nextRecordsUrl": "/services/data/v63.0/query/next-page",
        }
        second_response = Mock()
        second_response.json.return_value = {"records": [{"Id": "second"}]}
        session = Mock()
        session.get.side_effect = [first_response, second_response]

        records = downloader.execute_query(
            session, "https://example.my.salesforce.com", "63.0", "SELECT Id FROM Account", 15
        )

        self.assertEqual([record["Id"] for record in records], ["first", "second"])
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(
            session.get.call_args_list[0].kwargs["params"], {"q": "SELECT Id FROM Account"}
        )
        self.assertIsNone(session.get.call_args_list[1].kwargs["params"])


class DownloadTests(unittest.TestCase):
    def test_download_documents_streams_file_content(self):
        response = Mock()
        response.iter_content.return_value = [b"portfolio", b"-safe"]
        session = Mock()
        session.get.return_value = response
        records = [
            {
                "VersionData": "/services/data/v63.0/sobjects/ContentVersion/1/VersionData",
                "FileExtension": "txt",
                "ContentDocument": {"Title": "Example file"},
            }
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory) / "exports"
            count = downloader.download_documents(
                session,
                "https://example.my.salesforce.com",
                records,
                output_directory,
                timeout=15,
            )
            self.assertEqual(count, 1)
            content = (output_directory / "Example file.txt").read_bytes()
            self.assertEqual(content, b"portfolio-safe")


if __name__ == "__main__":
    unittest.main()
