"""Bounded spreadsheet inputs, explicit mappings and complete-row validation."""

from datetime import date
from io import BytesIO
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZipFile

from openpyxl import Workbook

from services.trade_contact_files import ContactFileError, read_file


def workbook_bytes(book):
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


class ContactFileReaderTest(unittest.TestCase):
    def refused(self, data, filename, code, **kwargs):
        with self.assertRaises(ContactFileError) as result:
            read_file(data, filename, **kwargs)
        self.assertEqual(result.exception.detail["code"], code)

    def test_csv_bom_accents_quotes_multiline_and_phone_text(self):
        raw = '\ufeffNombre,Teléfono,Correo\r\n"Técnico, Norte",+18095550123,info@example.com\r\n"Nombre\ncompuesto",0012345,\r\n'.encode()
        table = read_file(raw, "contacts.csv")
        self.assertEqual(table["suggested_mapping"], {"name": 0, "phone": 1, "email": 2})
        self.assertEqual(table["rows"], [
            {"row_number": 2, "values": ["Técnico, Norte", "+18095550123", "info@example.com"]},
            {"row_number": 3, "values": ["Nombre\ncompuesto", "0012345", ""]},
        ])
        self.assertEqual(len(table["file_sha256"]), 64)

    def test_explicit_separator_and_mapping_do_not_guess_unknown_headers(self):
        table = read_file(b"Business;Private number;Phone\nExample;+12025550123;00123\n", "contacts.csv", delimiter="semicolon")
        self.assertEqual(table["suggested_mapping"], {"phone": 2})
        self.assertEqual(table["rows"][0]["values"], ["Example", "+12025550123", "00123"])

    def test_file_row_cell_and_encoding_limits_refuse_instead_of_truncate(self):
        self.refused(b"name\n" + b"Example\n" * 251, "contacts.csv", "too_many_rows")
        self.refused(b"a" * (512 * 1024 + 1), "contacts.csv", "file_size")
        self.refused(b"name\n\xff\n", "contacts.csv", "utf8_required")
        self.refused(b"name\n" + b"a" * 4001, "contacts.csv", "table_limits")
        self.refused(b"name\nExample\n", "contacts.xls", "unsupported_file")
        self.refused(b"name\nExample,unexpected\n", "contacts.csv", "column_count", delimiter="comma")

    def test_workbook_visible_sheet_selection_text_numbers_dates_and_blanks(self):
        book = Workbook()
        book.active.title = "Other"
        book.active.append(["name"])
        book.active.append(["Other contact"])
        sheet = book.create_sheet("Contactos")
        sheet.append(["Nombre", "Teléfono", "Código postal", "Fecha revisada", "Provincia"])
        sheet.append(["Técnico", "+18095550123", 123, date(2026, 1, 15), None])
        sheet["C2"].number_format = "00000"
        hidden = book.create_sheet("Hidden")
        hidden.sheet_state = "hidden"
        data = workbook_bytes(book)
        table = read_file(data, "contacts.xlsx", sheet="Contactos")
        self.assertEqual(table["sheets"], ["Other", "Contactos"])
        self.assertEqual(table["sheet"], "Contactos")
        self.assertEqual(table["rows"][0]["values"], ["Técnico", "+18095550123", "00123", "2026-01-15", ""])
        self.refused(data, "contacts.xlsx", "unknown_sheet", sheet="Hidden")

    def test_formulas_and_distant_nonempty_cells_are_refused(self):
        book = Workbook()
        book.active.append(["name"])
        book.active.append(['=HYPERLINK("https://example.com","Name")'])
        self.refused(workbook_bytes(book), "contacts.xlsx", "formula_cells")
        book = Workbook()
        book.active.append(["name"])
        book.active["AO2"] = "Outside forty columns"
        self.refused(workbook_bytes(book), "contacts.xlsx", "table_limits")

    def test_expansion_and_active_workbook_content_are_refused(self):
        for filename, value, code in (
            ("xl/large.xml", b"a" * (8 * 1024 * 1024 + 1), "xlsx_expansion"),
            ("xl/vbaProject.bin", b"fake", "active_content"),
            ("xl/externalLinks/externalLink1.xml", b"fake", "active_content"),
        ):
            stream = BytesIO()
            with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("xl/workbook.xml", "<workbook/>")
                archive.writestr(filename, value)
            self.refused(stream.getvalue(), "contacts.xlsx", code)
        self.refused(b"not a workbook", "contacts.xlsx", "invalid_xlsx")

    def test_xml_entities_are_refused_before_workbook_loading(self):
        stream = BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", '<!DOCTYPE a [<!ENTITY x "unsafe">]><Types>&x;</Types>')
            archive.writestr("xl/workbook.xml", "<workbook/>")
        self.refused(stream.getvalue(), "contacts.xlsx", "invalid_xlsx")

    def test_large_numeric_identifiers_and_many_empty_xml_nodes_are_refused(self):
        book = Workbook()
        book.active.append(["name", "phone"])
        book.active.append(["Synthetic contact", 1234567890123456])
        self.refused(workbook_bytes(book), "contacts.xlsx", "numeric_precision")
        stream = BytesIO()
        with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types>" + "<x/>" * 100001 + "</Types>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        self.refused(stream.getvalue(), "contacts.xlsx", "table_limits")

    def test_actual_uppercase_and_nonstandard_worksheet_parts_are_preflighted(self):
        book = Workbook()
        book.active.append(["name"])
        book.active.append(["Synthetic contact"])
        original = workbook_bytes(book)
        for target in ("xl/worksheets/sheet1.XML", "xl/data/contact.part"):
            for overflow in ("<x>" * 129 + "</x>" * 129, "<x/>" * 100001):
                stream = BytesIO()
                with ZipFile(BytesIO(original)) as source, ZipFile(stream, "w", ZIP_DEFLATED) as archive:
                    for entry in source.infolist():
                        name, data = entry.filename, source.read(entry)
                        if name == "xl/worksheets/sheet1.xml":
                            name = target
                            data = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + overflow + '</worksheet>').encode()
                        elif name in {"xl/_rels/workbook.xml.rels", "[Content_Types].xml"}:
                            data = data.replace(b"/xl/worksheets/sheet1.xml", ("/" + target).encode())
                        archive.writestr(name, data)
                self.refused(stream.getvalue(), "contacts.xlsx", "table_limits")

    def test_lzma_member_is_refused_without_starting_its_decoder(self):
        stream = BytesIO()
        with ZipFile(stream, "w", ZIP_LZMA) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("xl/workbook.xml", "<workbook/>")
        with patch("zipfile.LZMADecompressor") as decoder:
            self.refused(stream.getvalue(), "contacts.xlsx", "invalid_xlsx")
            decoder.assert_not_called()

    def test_manifest_selected_shared_strings_are_bounded_and_thumbnail_stays_binary(self):
        book = Workbook()
        book.active.append(["name"])
        book.active.append(["Synthetic contact"])
        original = workbook_bytes(book)
        shared_type = b'application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml'
        for overflow in ("<x>" * 129 + "</x>" * 129, "<x/>" * 100001):
            stream = BytesIO()
            with ZipFile(BytesIO(original)) as source, ZipFile(stream, "w", ZIP_DEFLATED) as archive:
                for entry in source.infolist():
                    data = source.read(entry)
                    if entry.filename == "[Content_Types].xml":
                        data = data.replace(b'</Types>', b'<Override PartName="/xl/sharedStrings.part" ContentType="' + shared_type + b'"/></Types>')
                    archive.writestr(entry.filename, data)
                archive.writestr("xl/sharedStrings.part", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' + overflow + '</sst>')
            self.refused(stream.getvalue(), "contacts.xlsx", "table_limits")

        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (1, 1), "white").save(image, format="JPEG")
        stream = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(stream, "w", ZIP_DEFLATED) as archive:
            for entry in source.infolist():
                data = source.read(entry)
                if entry.filename == "[Content_Types].xml":
                    data = data.replace(b'</Types>', b'<Default Extension="jpeg" ContentType="image/jpeg"/></Types>')
                elif entry.filename == "_rels/.rels":
                    relation = b'<Relationship Id="thumbnail-regression" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/>'
                    data = data.replace(b'</Relationships>', relation + b'</Relationships>')
                archive.writestr(entry.filename, data)
            archive.writestr("docProps/thumbnail.jpeg", image.getvalue())
        table = read_file(stream.getvalue(), "contacts.xlsx")
        self.assertEqual(table["rows"], [{"row_number": 2, "values": ["Synthetic contact"]}])
