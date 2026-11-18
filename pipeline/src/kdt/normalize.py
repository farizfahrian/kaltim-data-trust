from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

MISSING_SENTINELS = {"", "-", "--", "—", "n/a", "na", "null", "none", "tad", "tidak ada data"}
AMBIGUOUS_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
AMBIGUOUS_COMMA_THOUSANDS_RE = re.compile(r"^-?\d{1,3}(,\d{3})+$")
FORMULA_PREFIXES = ("=", "+", "@")


@dataclass(frozen=True)
class ParsedNumeric:
    value: Decimal | None
    missing_reason: str | None = None
    error_code: str | None = None
    error_detail: str | None = None


def parse_numeric_value(
    raw_val: Any,
    *,
    locale_policy: str = "strict",
    require_integer: bool = False,
    unit_multiplier: Decimal = Decimal("1"),
) -> ParsedNumeric:
    """
    Deterministically parse a raw cell value without silent coercion.
    locale_policy:
      - 'strict': native int/float or unambiguous plain integer/decimal string; rejects '1.250' and '1,250'.
      - 'id_thousands': '.' is thousands separator, ',' is decimal separator.
      - 'en_thousands': ',' is thousands separator, '.' is decimal separator.
    """
    if raw_val is None:
        return ParsedNumeric(value=None, missing_reason="missing_in_source")

    if isinstance(raw_val, bool):
        return ParsedNumeric(
            value=None,
            error_code="invalid_boolean_numeric",
            error_detail=f"Nilai boolean {raw_val!r} tidak valid untuk kolom numerik.",
        )

    if isinstance(raw_val, int):
        dec = Decimal(raw_val) * unit_multiplier
        return _check_constraints(dec, require_integer=require_integer, raw_repr=repr(raw_val))

    if isinstance(raw_val, float):
        # Guard against float artifacts like 286.99099999999999
        dec = Decimal(str(raw_val))
        # Normalize exact integer floats like 223.0 -> 223
        if dec == dec.to_integral_value():
            dec = dec.to_integral_value()
        dec = dec * unit_multiplier
        return _check_constraints(dec, require_integer=require_integer, raw_repr=repr(raw_val))

    text = str(raw_val).strip()
    if text.lower() in MISSING_SENTINELS:
        return ParsedNumeric(value=None, missing_reason="missing_in_source")

    if text.startswith(FORMULA_PREFIXES) or (text.startswith("-") and len(text) > 1 and not text[1].isdigit()):
        return ParsedNumeric(
            value=None,
            error_code="unsafe_or_unevaluated_formula",
            error_detail=f"Ekspresi formula mentah ditolak: {text[:40]!r}",
        )

    if "%" in text:
        return ParsedNumeric(
            value=None,
            error_code="unexpected_percent_symbol",
            error_detail=f"Simbol '%' mentah memerlukan aturan konversi eksplisit: {text!r}",
        )

    if locale_policy == "strict":
        if AMBIGUOUS_THOUSANDS_RE.match(text) or AMBIGUOUS_COMMA_THOUSANDS_RE.match(text):
            return ParsedNumeric(
                value=None,
                error_code="ambiguous_locale_number",
                error_detail=(
                    f"String numerik {text!r} ambigu antara pemisah ribuan vs desimal pada mode strict."
                ),
            )
        if "," in text:
            return ParsedNumeric(
                value=None,
                error_code="ambiguous_comma_decimal",
                error_detail=f"String numerik berkoma {text!r} ditolak tanpa kebijakan locale eksplisit.",
            )
        cleaned = text
    elif locale_policy == "id_thousands":
        cleaned = text.replace(".", "").replace(",", ".")
    elif locale_policy == "en_thousands":
        cleaned = text.replace(",", "")
    else:
        return ParsedNumeric(
            value=None,
            error_code="unknown_locale_policy",
            error_detail=f"Kebijakan locale tidak dikenal: {locale_policy!r}",
        )

    try:
        dec = Decimal(cleaned) * unit_multiplier
    except InvalidOperation:
        return ParsedNumeric(
            value=None,
            error_code="non_numeric_string",
            error_detail=f"Gagal mengurai nilai numerik dari {text!r}.",
        )

    return _check_constraints(dec, require_integer=require_integer, raw_repr=repr(raw_val))


def _check_constraints(dec: Decimal, *, require_integer: bool, raw_repr: str) -> ParsedNumeric:
    if not dec.is_finite():
        return ParsedNumeric(
            value=None,
            error_code="non_finite_numeric",
            error_detail=f"Nilai numerik tak hingga/NaN tidak diizinkan: {raw_repr}",
        )
    if require_integer and dec != dec.to_integral_value():
        return ParsedNumeric(
            value=None,
            error_code="non_integer_for_integral_metric",
            error_detail=(
                f"Metrik cacah bulat menerima nilai pecahan {dec} (kemungkinan korupsi pemisah ribuan pada {raw_repr})."
            ),
        )
    if require_integer:
        dec = dec.to_integral_value()
    return ParsedNumeric(value=dec)


def parse_year(raw_year: Any) -> int | None:
    if raw_year is None:
        return None
    if isinstance(raw_year, bool):
        return None
    if isinstance(raw_year, int):
        return raw_year if 1990 <= raw_year <= 2100 else None
    if isinstance(raw_year, float) and raw_year.is_integer():
        yr = int(raw_year)
        return yr if 1990 <= yr <= 2100 else None
    text = str(raw_year).strip().upper()
    if text.startswith("TH "):
        text = text[3:].strip()
    elif text.startswith("TAHUN "):
        text = text[6:].strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        yr = int(text)
        return yr if 1990 <= yr <= 2100 else None
    return None
