"""Provider CSV/XLSX contracts, runnable without private application imports."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import load_workbook
from rest_framework.exceptions import ValidationError

from apps.providers.import_tables import read_text_table, read_upload, template_xlsx, csv_bytes

HEAD = "api_ref,name,base_url,api_key\n"


class ProviderImportParserTests(SimpleTestCase):
    def test_csv_and_pasted_tsv_preserve_keys_and_quotes(self):
        rows = read_text_table('\ufeff' + HEAD + 'api-one,"Name, quoted",https://api.example.com/v1, key-with-spaces \n')
        self.assertEqual(rows[0]["name"], "Name, quoted")
        self.assertEqual(rows[0]["api_key"], " key-with-spaces ")
        self.assertEqual(read_text_table(HEAD.replace(",", "\t") + 'one\tOne\thttps://example.com\tsecret')[0]["api_ref"], "one")

    def workbook(self, *, models=False, formula=False):
        book = load_workbook(io.BytesIO(template_xlsx()))
        book["APIs"].append(["api-one", "=1+1" if formula else "Name & company", "https://example.com/v1", "secret-key"])
        if models:
            book.create_sheet("Models").append(["api_ref", "upstream_model"])
        stream = io.BytesIO()
        book.save(stream)
        book.close()
        return SimpleUploadedFile("apis.xlsx", stream.getvalue())

    def test_excel_template_has_no_model_sheet_and_round_trips(self):
        book = load_workbook(io.BytesIO(template_xlsx()))
        self.assertEqual(book.sheetnames, ["APIs", "Guide"])
        book.close()
        self.assertEqual(read_upload(self.workbook())[0]["name"], "Name & company")

    def test_rejects_model_sheets_and_columns(self):
        with self.assertRaises(ValidationError):
            read_upload(self.workbook(models=True))
        with self.assertRaises(ValidationError):
            read_text_table(HEAD.rstrip() + ",models\n")

    def test_rejects_formulas_bad_files_and_over_limit(self):
        for upload in [self.workbook(formula=True), SimpleUploadedFile("broken.xlsx", b"bad"), SimpleUploadedFile("big.csv", b"x" * (2_097_152 + 1))]:
            with self.assertRaises(ValidationError):
                read_upload(upload)
        with self.assertRaises(ValidationError):
            read_text_table(HEAD + "x,Name,https://example.com,key\n" * 101)

    def test_safe_report_neutralizes_spreadsheet_formulas(self):
        value = csv_bytes(["name"], [["=HYPERLINK(\"unsafe\")"], [" @SUM(1)"]]).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", value)
        self.assertIn("' @SUM", value)
