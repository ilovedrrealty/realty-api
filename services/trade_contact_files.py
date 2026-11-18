"""Read small CSV/XLSX files without retaining files or importing contacts."""

import csv
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO, StringIO
import math
import posixpath
import json
from pathlib import PurePosixPath
import re
import unicodedata
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from pydantic import ValidationError

from schemas.trade_contact_files import CELL_LIMIT, IMPORT_FIELDS
from schemas.trade_contacts import TradeContactEntry, TradeContactImport
from services.trade_contacts import SourceNotesOverflow, contact_identity, import_entries

FILE_LIMIT = 512 * 1024
ROW_LIMIT = 250
COLUMN_LIMIT = 40
EXPANSION_LIMIT = 8 * 1024 * 1024


class ContactFileError(ValueError):
    def __init__(self, code, row=None):
        super().__init__(code)
        self.detail = {"code": code}
        if row is not None:
            self.detail["row"] = row


def normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "_", "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                                             if not unicodedata.combining(char))).strip("_")


ALIASES = {
    "name": ("name", "business_name", "company", "nombre", "empresa", "nombre_comercial"),
    "category": ("category", "trade", "categoria", "oficio", "profesion"),
    "subtype": ("subtype", "specialty", "especialidad"),
    "province": ("province", "provincia"),
    "locality": ("locality", "city", "municipality", "ciudad", "municipio", "localidad"),
    "service_area": ("service_area", "area_de_servicio", "zona_de_servicio"),
    "contact_name": ("contact_name", "contact", "contacto", "nombre_contacto"),
    "contact_title": ("contact_title", "cargo"),
    "phone": ("phone", "telephone", "tel", "telefono", "celular"),
    "email": ("email", "e_mail", "correo", "correo_electronico"),
    "website": ("website", "web", "sitio_web", "pagina_web"),
    "address": ("address", "direccion"),
    "postal_code": ("postal_code", "zip", "codigo_postal"),
    "external_id": ("external_id", "registry_id", "registro", "rnc"),
    "source_authority": ("source_authority", "source_name", "fuente", "nombre_fuente"),
    "source_url": ("source_url", "source_link", "enlace_fuente"),
    "verified_on": ("verified_on", "date_checked", "fecha_verificacion", "fecha_revisada"),
    "verification_status": ("verification_status", "source_type", "tipo_fuente"),
}


def suggest(columns):
    normalized = [normalize_header(value) for value in columns]
    used = set()
    result = {}
    for field, aliases in ALIASES.items():
        match = next((index for index, name in enumerate(normalized) if name in aliases and index not in used), None)
        if match is not None:
            result[field] = match
            used.add(match)
    return result


def table_rows(iterator):
    columns = None
    rows = []
    for number, values in iterator:
        values = [value.strip() for value in values]
        while values and not values[-1]:
            values.pop()
        if not any(values):
            continue
        if len(values) > COLUMN_LIMIT or any(len(value) > CELL_LIMIT for value in values):
            raise ContactFileError("table_limits", number)
        if columns is None:
            if any(len(value) > 240 for value in values):
                raise ContactFileError("table_limits", number)
            columns = values
        else:
            if len(values) > len(columns):
                raise ContactFileError("column_count", number)
            rows.append({"row_number": number, "values": values + [""] * (len(columns) - len(values))})
            if len(rows) > ROW_LIMIT:
                raise ContactFileError("too_many_rows")
    if not columns or not rows:
        raise ContactFileError("empty_file")
    if len(json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        raise ContactFileError("table_limits")
    return columns, rows


def csv_rows(data, delimiter):
    try:
        value = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ContactFileError("utf8_required") from None
    if "\0" in value:
        raise ContactFileError("invalid_file")
    if delimiter == "auto":
        try:
            delimiter = csv.Sniffer().sniff(value[:32768], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    else:
        delimiter = {"comma": ",", "semicolon": ";", "tab": "\t"}[delimiter]
    try:
        reader = csv.reader(StringIO(value, newline=""), delimiter=delimiter, strict=True)
        def records():
            previous = 0
            for row in reader:
                yield previous + 1, row
                previous = reader.line_num
        return table_rows(records())
    except csv.Error:
        raise ContactFileError("invalid_csv") from None


def spreadsheet(data, selected):
    try:
        from defusedxml.ElementTree import iterparse
        node_count = 0
        with ZipFile(BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 512 or sum(file.file_size for file in files) > EXPANSION_LIMIT:
                raise ContactFileError("xlsx_expansion")
            for file in files:
                if file.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise ContactFileError("invalid_xlsx")
                path = PurePosixPath(file.filename)
                if path.is_absolute() or ".." in path.parts or file.flag_bits & 1:
                    raise ContactFileError("invalid_xlsx")
                if file.filename.lower().endswith("vbaproject.bin") or file.filename.lower().startswith("xl/externallinks/"):
                    raise ContactFileError("active_content")
            names = {file.filename for file in files}
            if len(names) != len(files) or not {"[Content_Types].xml", "xl/workbook.xml"} <= names:
                raise ContactFileError("invalid_xlsx")
            # Relationships can target worksheet XML with a nonstandard name
            # or extension. Classify those actual ZIP parts before parsing data.
            def scan_xml(name, worksheet=False):
                nonlocal node_count
                relationships = []
                with archive.open(name) as stream:
                    depth = 0
                    for event, element in iterparse(stream, events=("start", "end")):
                        if event == "start":
                            node_count += 1
                            depth += 1
                            if node_count > 100000 or depth > 128:
                                raise ContactFileError("table_limits")
                            continue
                        depth -= 1
                        tag = element.tag.rsplit("}", 1)[-1]
                        if tag == "Relationship":
                            relationships.append(dict(element.attrib))
                            if len(relationships) > 1024:
                                raise ContactFileError("table_limits")
                        if worksheet and tag == "c":
                            if any(child.tag.rsplit("}", 1)[-1] in {"v", "is", "f"} for child in element):
                                reference = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", element.get("r", ""))
                                if reference is None:
                                    raise ContactFileError("invalid_xlsx")
                                column = 0
                                for letter in reference[1]:
                                    column = column * 26 + ord(letter) - ord("A") + 1
                                if column > COLUMN_LIMIT or int(reference[2]) > 2000:
                                    raise ContactFileError("table_limits")
                            element.clear()
                        elif not worksheet:
                            element.clear()
                # All XML references die on return, before workbook loading.
                return relationships

            relationship_parts = {name for name in names if name.lower().endswith(".rels")}
            xml_parts = {name for name in names if name.lower().endswith(".xml")}
            worksheet_parts = set()
            for name in relationship_parts:
                for relation in scan_xml(name):
                    kind = relation.get("Type", "").rsplit("/", 1)[-1].lower()
                    if kind in {"externallink", "oleobject", "vbaproject", "package"}:
                        raise ContactFileError("active_content")
                    if relation.get("TargetMode", "").lower() == "external":
                        # Ordinary clickable website cells are retained as text;
                        # their targets are never requested by this reader.
                        if kind != "hyperlink":
                            raise ContactFileError("active_content")
                        continue
                    target = relation.get("Target", "")
                    if not target:
                        raise ContactFileError("invalid_xlsx")
                    base = str(PurePosixPath(name).parent.parent)
                    resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(base, target))
                    if resolved not in names or resolved.startswith("../"):
                        raise ContactFileError("invalid_xlsx")
                    if kind not in {"image", "printersettings"}:
                        xml_parts.add(resolved)
                    if kind == "worksheet":
                        worksheet_parts.add(resolved)
            for name in xml_parts - relationship_parts:
                scan_xml(name, worksheet=name in worksheet_parts or name.lower().startswith("xl/worksheets/"))
        from openpyxl import load_workbook
        from openpyxl.xml.functions import DEFUSEDXML
        if not DEFUSEDXML:
            raise ContactFileError("parser_unavailable")
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=False, keep_links=False, keep_vba=False)
        try:
            sheets = [sheet.title for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
            if not sheets or len(sheets) > 100 or any(len(name) > 80 for name in sheets):
                raise ContactFileError("invalid_xlsx")
            name = selected or sheets[0]
            if name not in sheets:
                raise ContactFileError("unknown_sheet")
            sheet = workbook[name]
            # Do not trust worksheet dimension metadata from third-party exporters.
            sheet.reset_dimensions()
            def records():
                for number, cells in enumerate(sheet.iter_rows(max_col=COLUMN_LIMIT), start=1):
                    if number > 2000:
                        raise ContactFileError("table_limits")
                    values = []
                    for cell in cells:
                        value = cell.value
                        if cell.data_type == "f":
                            raise ContactFileError("formula_cells", number)
                        if value is None:
                            value = ""
                        elif isinstance(value, (datetime, date)):
                            value = value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
                        elif isinstance(value, bool):
                            value = "true" if value else "false"
                        elif isinstance(value, (int, float)):
                            if not math.isfinite(value):
                                raise ContactFileError("invalid_cell", number)
                            if abs(value) >= 10 ** 15:
                                raise ContactFileError("numeric_precision", number)
                            if isinstance(value, int) or value.is_integer():
                                value = str(int(value))
                                if re.fullmatch(r"0{1,30}", cell.number_format or ""):
                                    value = value.zfill(len(cell.number_format))
                            else:
                                value = str(value)
                        else:
                            value = str(value)
                        values.append(value)
                        if len(values) > COLUMN_LIMIT:
                            raise ContactFileError("table_limits", number)
                    yield number, values
            columns, rows = table_rows(records())
            return columns, rows, sheets, name
        finally:
            workbook.close()
    except ContactFileError:
        raise
    except ImportError:
        raise ContactFileError("parser_unavailable") from None
    except Exception:
        raise ContactFileError("invalid_xlsx") from None


def read_file(data, filename, sheet=None, delimiter="auto"):
    if len(data) > FILE_LIMIT:
        raise ContactFileError("file_size")
    suffix = PurePosixPath(filename or "").suffix.lower()
    if suffix == ".csv":
        columns, rows = csv_rows(data, delimiter)
        sheets, selected = [], None
    elif suffix == ".xlsx":
        columns, rows, sheets, selected = spreadsheet(data, sheet)
    else:
        raise ContactFileError("unsupported_file")
    return {"columns": columns, "rows": rows, "file_sha256": sha256(data).hexdigest(),
            "sheets": sheets, "sheet": selected, "suggested_mapping": suggest(columns)}


def preview_mapping(db, body):
    if any(index >= len(body.table.columns) for index in body.mapping.values()):
        raise ContactFileError("invalid_mapping")
    entries, issues, issue_count = [], [], 0
    for row in body.table.rows:
        if len(row.values) != len(body.table.columns):
            raise ContactFileError("invalid_mapping")
        values = {key: value.strip() for key, value in body.defaults.items() if value.strip()}
        for key, index in body.mapping.items():
            if row.values[index].strip():
                values[key] = row.values[index].strip()
        values["source_details"] = {"import_file_sha256": body.table.file_sha256,
                                    "import_row": str(row.row_number)}
        if body.table.sheet:
            values["source_details"]["import_sheet"] = body.table.sheet
        try:
            entries.append(TradeContactEntry.model_validate(values))
        except ValidationError as error:
            for problem in error.errors(include_input=False, include_url=False):
                issue_count += 1
                field = str(problem["loc"][0]) if problem["loc"] else "name"
                if len(issues) < 50:
                    issues.append({"row": row.row_number, "field": field if field in IMPORT_FIELDS else "name",
                                   "code": "required" if problem["type"] == "missing" else "invalid"})
    if issues:
        return {"entries": [], "issues": issues, "issue_count": issue_count, "result": None}
    try:
        result = import_entries(db, TradeContactImport(dry_run=True, entries=entries, preserve_source_details=True))
    except SourceNotesOverflow as error:
        conflicts = [{"row": row.row_number, "field": "source_details", "code": "note_limit"}
                     for row, entry in zip(body.table.rows, entries) if contact_identity(entry) in error.identities]
        return {"entries": [], "issues": conflicts[:50], "issue_count": len(conflicts), "result": None}
    return {"entries": entries, "issues": [], "issue_count": 0, "result": result}
