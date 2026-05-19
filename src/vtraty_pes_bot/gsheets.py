import itertools
from enum import Enum
from functools import total_ordering

import aiohttp


# @NOTE: total_ordering generates __gt__, __le__, __ge__ from __lt__ and __eq__.
# The previous manual implementation had a bug where `a > a` returned True
# because __gt__ was `not (self < other)` instead of a proper greater-than check.
@total_ordering
class CustomEnum(Enum):
    # Source: https://stackoverflow.com/a/71839532
    def __lt__(self, other: "CustomEnum"):
        return self.value < other.value


async def get_gsheet_prompt(spreadsheet_id, api_key, sheet_name="table"):
    # Construct the URL for the Google Sheets API
    base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{sheet_name}"

    async def _get_list_prompt(rang: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}!{rang}?alt=json&key={api_key}") as resp:
                data = await resp.json()
                # @NOTE: Rows with <2 columns are skipped (incomplete entries). Rows with
                # >2 columns use the first two (extra columns are metadata/notes in the sheet).
                # If the API returns an error (no "values" key), we return empty — the LLM
                # prompt will lack reference data but won't crash.
                lines = []
                for row in data.get("values", []):
                    if len(row) >= 2:
                        lines.append(f"{row[0].strip()} - {row[1].strip()}")
                return "\n".join(lines)

    text = "\nCommon keywords:\n"
    text += await _get_list_prompt("D2:E")
    text += "\nNon-exhaustive list of common vehicles for reference:\n"
    text += await _get_list_prompt("A2:B")
    return text


async def get_vehicle_types(spreadsheet_id, api_key, logger, sheet_name="table"):
    # Construct the URL for the Google Sheets API
    base_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{sheet_name}"

    async def _get(rang: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}!{rang}?alt=json&key={api_key}") as resp:
                data = await resp.json()
                # @NOTE: Each row should contain a single vehicle type name. chain(*rows)
                # flattens them. If the API errors (no "values"), returns empty list — caller
                # will produce a VehicleTypes enum with only UNKNOWN.
                return list(v.strip() for v in itertools.chain(*data.get("values", [])))

    raw_types = await _get("C2:C")
    # @NOTE: Normalize to uppercase for consistent enum member names. Warn if the sheet
    # has mixed-case entries — that likely indicates a typo or formatting issue.
    type_strings = []
    for s in raw_types:
        upper = s.upper()
        if upper != s:
            logger.warning("Vehicle type '%s' from Google Sheet is not uppercase, normalizing to '%s'.", s, upper)
        type_strings.append(upper)
    # Deduplicate while preserving order — the Google Sheet may contain
    # duplicate entries (especially after normalization), which would crash Python's Enum constructor.
    type_strings = list(dict.fromkeys(type_strings))
    if "UNKNOWN" in type_strings:
        return Enum("VehicleTypes", type_strings, type=CustomEnum)
    return Enum("VehicleTypes", type_strings + ["UNKNOWN"], type=CustomEnum)
