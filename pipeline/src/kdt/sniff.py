from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_RAW_FILE_BYTES = 25 * 1024 * 1024  # 25 MB safety limit
MAX_UNCOMPRESSED_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB zip-bomb guard


class FileSecurityOrFormatError(ValueError):
    """Raised when an input file is corrupt, unsupported, or unsafe (e.g. VBA macros / zip bomb)."""


@dataclass(frozen=True)
class SniffResult:
    path: Path
    sha256: str
    size_bytes: int
    actual_format: str  # "XLSX" | "CSV"
    reported_format: str | None
    extension: str
    warnings: list[str] = field(default_factory=list)


def sniff_file(path: Path, reported_format: str | None = None) -> SniffResult:
    if not path.exists() or not path.is_file():
        raise FileSecurityOrFormatError(f"File tidak ditemukan: {path.name}")

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        raise FileSecurityOrFormatError(f"File kosong (0 byte): {path.name}")
    if size_bytes > MAX_RAW_FILE_BYTES:
        raise FileSecurityOrFormatError(f"Ukuran file melebihi batas aman ({size_bytes} byte)")

    raw_bytes = path.read_bytes()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    ext = path.suffix.lower().lstrip(".")
    warnings: list[str] = []

    if raw_bytes.startswith(b"PK\x03\x04"):
        actual_format = _verify_xlsx_zip(path)
    elif raw_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        raise FileSecurityOrFormatError(
            "Format biner OLE2 lama (.xls) terdeteksi; konversi eksplisit ke .xlsx/.csv diperlukan sebelum impor."
        )
    else:
        actual_format = _verify_csv_text(raw_bytes)

    if reported_format:
        rep_norm = reported_format.strip().upper()
        if rep_norm in {"CSV", "XLSX"} and rep_norm != actual_format:
            warnings.append(
                f"Metadata melaporkan format '{rep_norm}', tetapi inspeksi byte membuktikan '{actual_format}'."
            )
    if ext in {"csv", "xlsx"} and ext.upper() != actual_format:
        warnings.append(
            f"Ekstensi nama file '.{ext}' tidak sesuai dengan format byte aktual '{actual_format}'."
        )

    return SniffResult(
        path=path,
        sha256=sha256,
        size_bytes=size_bytes,
        actual_format=actual_format,
        reported_format=reported_format,
        extension=ext,
        warnings=warnings,
    )


def _verify_xlsx_zip(path: Path) -> str:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            total_uncompressed = sum(info.file_size for info in zf.infolist())
            if total_uncompressed > MAX_UNCOMPRESSED_ZIP_BYTES:
                raise FileSecurityOrFormatError(
                    f"Kontainer ZIP ditolak karena ukuran dekompresi tidak wajar ({total_uncompressed} byte)."
                )
            for name in names:
                lower = name.lower()
                if "vbaproject.bin" in lower or lower.endswith(".bin") and "macros" in lower:
                    raise FileSecurityOrFormatError(
                        "File Excel mengandung proyek makro VBA (xl/vbaProject.bin) dan ditolak demi keamanan."
                    )
            if "xl/workbook.xml" not in names:
                raise FileSecurityOrFormatError(
                    "Kontainer ZIP bukan workbook XLSX yang valid (xl/workbook.xml tidak ditemukan)."
                )
    except zipfile.BadZipFile as exc:
        raise FileSecurityOrFormatError("Kontainer ZIP/XLSX rusak (corrupt).") from exc
    return "XLSX"


def _verify_csv_text(raw_bytes: bytes) -> str:
    if b"\x00" in raw_bytes[:4096]:
        raise FileSecurityOrFormatError("Byte biner tak dikenal terdeteksi; bukan CSV/XLSX yang valid.")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FileSecurityOrFormatError("Encoding teks bukan UTF-8 yang valid.") from exc

    stripped = text.lstrip()[:256].lower()
    if stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        raise FileSecurityOrFormatError("Halaman HTML terdeteksi (kemungkinan halaman error), bukan CSV.")

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise FileSecurityOrFormatError("File teks tidak memiliki baris data.")
    header = lines[0]
    if "," not in header and ";" not in header:
        raise FileSecurityOrFormatError("Header CSV tidak memiliki delimiter koma atau titik-koma.")
    return "CSV"
