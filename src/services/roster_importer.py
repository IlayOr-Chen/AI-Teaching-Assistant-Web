"""
Purpose:
    Imports and manages the student class roster from Excel (`.xlsx`) files into
    Firebase Realtime Database (`/students`), automatically parsing Hebrew columns
    (`שם פרטי`, `שם משפחה`, `דוא"ל`, `קבוצות`), concatenating `full_name`, and
    gracefully handling empty or `NaN` group cells without crashing.
Inputs:
    `FirebaseRepository` persistence instance and Excel file paths or byte streams.
Outputs:
    `RosterImporter` class providing Excel parsing, Firebase RTDB persistence,
    and searchable student roster queries.
"""

from __future__ import annotations

import io
import math
import re
import time
from pathlib import Path
from typing import Any

import openpyxl

from src.repository.firebase_repository import FirebaseRepository


class RosterImporter:
    """
    Purpose:
        Parses student participant Excel files (specifically supporting the Hebrew
        Moodle/Course format in `/assets/courseid_130158_participants.xlsx` as well
        as English headers) and persists student records under `/students` in Firebase RTDB.
    Inputs:
        repository (FirebaseRepository): Firebase RTDB repository for `/students`.
    Outputs:
        `RosterImporter` service instance.
    """

    FIRST_NAME_HEADERS = {"שם פרטי", "first name", "firstname", "first_name"}
    LAST_NAME_HEADERS = {"שם משפחה", "last name", "lastname", "last_name", "surname"}
    FULL_NAME_HEADERS = {"שם מלא", "full name", "fullname", "full_name", "name"}
    EMAIL_HEADERS = {'דוא"ל', "דואל", "אימייל", "email", "e-mail", "mail"}
    GROUP_HEADERS = {"קבוצות", "קבוצה", "group", "groups", "section"}

    def __init__(self, repository: FirebaseRepository) -> None:
        """
        Purpose:
            Initialize `RosterImporter` with the target Firebase repository.
        Inputs:
            repository (FirebaseRepository): Persistence repository.
        Outputs:
            None.
        """
        self.repository = repository

    @staticmethod
    def email_to_student_id(email: str) -> str:
        """
        Purpose:
            Derive a deterministic, Firebase-safe student identifier from an email address.
        Inputs:
            email (str): Student email address.
        Outputs:
            str: Sanitized student ID (e.g., `stu_guygg197_gmail_com`).
        """
        normalized = email.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
        return f"stu_{slug}" if slug else "stu_unknown"

    @staticmethod
    def normalize_group(raw_group: Any) -> str:
        """
        Purpose:
            Clean and normalize a raw Excel cell value from the `קבוצות` (Groups) column,
            gracefully converting `None`, `float('nan')`, `"NaN"`, or blank cells to `""`,
            and converting integer-valued floats like `3.0` to `"3"`.
        Inputs:
            raw_group (Any): Cell value read from `openpyxl` (None, float, int, or str).
        Outputs:
            str: Normalized group string (e.g., `"3"`, or `""` when unassigned/NaN).
        """
        if raw_group is None:
            return ""

        if isinstance(raw_group, float):
            # Explicitly guard against IEEE 754 NaN values from spreadsheets/pandas
            if math.isnan(raw_group):
                return ""
            if raw_group.is_integer():
                return str(int(raw_group))
            return str(raw_group).strip()

        if isinstance(raw_group, int) and not isinstance(raw_group, bool):
            return str(raw_group)

        text = str(raw_group).strip()
        if not text or text.lower() in {"nan", "none", "null", "n/a", "-"}:
            return ""

        # Normalize string representations of whole-number floats such as "3.0" -> "3"
        if re.fullmatch(r"\d+\.0+", text):
            return text.split(".", 1)[0]

        return text

    @classmethod
    def _resolve_column_indices(cls, header_row: tuple[Any, ...]) -> dict[str, int | None]:
        """
        Purpose:
            Locate column indices for first name, last name, full name, email, and group
            from the first row of an Excel worksheet.
        Inputs:
            header_row (tuple[Any, ...]): Tuple of raw header cell values.
        Outputs:
            dict[str, int | None]: Mapped column index dictionary. Raises `ValueError`
            if mandatory name or email columns are missing.
        """
        indices: dict[str, int | None] = {
            "first_name": None,
            "last_name": None,
            "full_name": None,
            "email": None,
            "group": None,
        }

        for idx, raw_cell in enumerate(header_row):
            if raw_cell is None:
                continue
            header = str(raw_cell).strip().lower()
            if header in cls.FIRST_NAME_HEADERS:
                indices["first_name"] = idx
            elif header in cls.LAST_NAME_HEADERS:
                indices["last_name"] = idx
            elif header in cls.FULL_NAME_HEADERS:
                indices["full_name"] = idx
            elif header in cls.EMAIL_HEADERS:
                indices["email"] = idx
            elif header in cls.GROUP_HEADERS:
                indices["group"] = idx

        has_split_name = (
            indices["first_name"] is not None and indices["last_name"] is not None
        )
        has_full_name = indices["full_name"] is not None
        if not (has_split_name or has_full_name):
            raise ValueError(
                "Excel header is missing required student name columns ('שם פרטי' and 'שם משפחה')."
            )
        if indices["email"] is None:
            raise ValueError(
                "Excel header is missing required student email column ('דוא\"ל')."
            )

        return indices

    def parse_excel_rows(
        self, source: str | Path | bytes
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Purpose:
            Parse student records from an `.xlsx` file path or raw bytes without
            mutating the database yet.
        Inputs:
            source (str | Path | bytes): Path to `.xlsx` file or in-memory `.xlsx` bytes.
        Outputs:
            tuple[list[dict[str, Any]], int]: `(parsed_students, skipped_row_count)`.
        """
        try:
            if isinstance(source, (bytes, bytearray)):
                workbook = openpyxl.load_workbook(
                    io.BytesIO(source), data_only=True
                )
            else:
                path_obj = Path(source)
                if not path_obj.is_file():
                    raise FileNotFoundError(f"Excel file not found: {path_obj}")
                workbook = openpyxl.load_workbook(path_obj, data_only=True)
        except FileNotFoundError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Failed to open Excel (.xlsx) workbook: {exc}"
            ) from exc

        sheet = workbook.active
        if sheet is None:
            raise ValueError("Excel workbook contains no active worksheet.")

        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValueError("Excel worksheet is empty.")

        col_map = self._resolve_column_indices(rows[0])
        parsed_students: list[dict[str, Any]] = []
        skipped_rows = 0
        imported_at = int(time.time())

        for row in rows[1:]:
            if not row or all(cell is None or str(cell).strip() == "" for cell in row):
                continue

            email_idx = col_map["email"]
            raw_email = (
                row[email_idx]
                if email_idx is not None and email_idx < len(row)
                else None
            )
            clean_email = str(raw_email).strip().lower() if raw_email is not None else ""
            if not clean_email or "@" not in clean_email:
                skipped_rows += 1
                continue

            first_name = ""
            last_name = ""
            if col_map["first_name"] is not None and col_map["first_name"] < len(row):
                raw_fn = row[col_map["first_name"]]
                first_name = str(raw_fn).strip() if raw_fn is not None else ""
            if col_map["last_name"] is not None and col_map["last_name"] < len(row):
                raw_ln = row[col_map["last_name"]]
                last_name = str(raw_ln).strip() if raw_ln is not None else ""

            if first_name or last_name:
                # Concatenate 'שם פרטי' + 'שם משפחה' into a clean single-spaced full_name
                full_name = f"{first_name} {last_name}".strip()
            else:
                fn_idx = col_map["full_name"]
                raw_full = (
                    row[fn_idx] if fn_idx is not None and fn_idx < len(row) else None
                )
                full_name = str(raw_full).strip() if raw_full is not None else ""

            if not full_name:
                skipped_rows += 1
                continue

            group_idx = col_map["group"]
            raw_group = (
                row[group_idx]
                if group_idx is not None and group_idx < len(row)
                else None
            )
            clean_group = self.normalize_group(raw_group)
            student_id = self.email_to_student_id(clean_email)

            parsed_students.append(
                {
                    "student_id": student_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": full_name,
                    "email": clean_email,
                    "group": clean_group,
                    "imported_at": imported_at,
                }
            )

        return parsed_students, skipped_rows

    def import_from_excel(
        self, source: str | Path | bytes, replace_existing: bool = True
    ) -> dict[str, Any]:
        """
        Purpose:
            Parse an Excel class roster and persist all student records into
            Firebase RTDB under `/students`.
        Inputs:
            source (str | Path | bytes): Path or raw bytes of the `.xlsx` file.
            replace_existing (bool): When True, replaces `/students` cleanly; when False,
                merges via `update('/students', ...)`.
        Outputs:
            dict[str, Any]: Summary containing `total_imported`, `unassigned_group_count`,
            `skipped_rows`, and the list of `students`.
        """
        parsed_students, skipped_rows = self.parse_excel_rows(source)
        students_dict = {stu["student_id"]: stu for stu in parsed_students}

        if replace_existing:
            self.repository.set("/students", students_dict)
        else:
            self.repository.update("/students", students_dict)

        unassigned_count = sum(1 for stu in parsed_students if not stu["group"])
        return {
            "total_imported": len(parsed_students),
            "unassigned_group_count": unassigned_count,
            "skipped_rows": skipped_rows,
            "students": parsed_students,
        }

    def list_students(
        self,
        search_query: str | None = None,
        group_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Purpose:
            Retrieve all imported students from `/students` in Firebase RTDB,
            sorted alphabetically by `full_name` and optionally filtered by search query
            or group number.
        Inputs:
            search_query (str | None): Optional case-insensitive substring match against
                `full_name` or `email`.
            group_filter (str | None): Optional exact group filter (or `"unassigned"`).
        Outputs:
            list[dict[str, Any]]: Sorted list of matching student dictionaries.
        """
        raw_students = self.repository.get("/students")
        if not isinstance(raw_students, dict):
            return []

        results: list[dict[str, Any]] = []
        q = search_query.strip().lower() if search_query else ""
        g_filter = group_filter.strip() if group_filter is not None else None

        for stu in raw_students.values():
            if not isinstance(stu, dict):
                continue

            if q:
                name_val = str(stu.get("full_name", "")).lower()
                email_val = str(stu.get("email", "")).lower()
                if q not in name_val and q not in email_val:
                    continue

            if g_filter:
                stu_group = str(stu.get("group", ""))
                if g_filter.lower() == "unassigned":
                    if stu_group != "":
                        continue
                elif stu_group != g_filter:
                    continue

            results.append(stu)

        results.sort(key=lambda item: str(item.get("full_name", "")))
        return results

    def get_student(self, student_id: str) -> dict[str, Any] | None:
        """
        Purpose:
            Fetch a single student record by `student_id` from `/students/{student_id}`.
        Inputs:
            student_id (str): Target student identifier.
        Outputs:
            dict[str, Any] | None: Student record dictionary if found, else `None`.
        """
        if not student_id:
            return None
        record = self.repository.get(f"/students/{student_id.strip()}")
        return record if isinstance(record, dict) else None
