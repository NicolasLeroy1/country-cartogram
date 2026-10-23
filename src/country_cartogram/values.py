"""Shared region/value CSV input for maps, time series and extrusion."""

import csv
import math
from pathlib import Path
import warnings

from .maps import region_id


def read_table(path, required):
    """Read one CSV dialect and validate its header and row widths."""
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        if not fields or not set(required).issubset(fields):
            raise ValueError(f"CSV needs columns: {sorted(required)}")
        if len(fields) != len(set(fields)):
            raise ValueError("Duplicate CSV column names")
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Line {line}: CSV row does not match its header")
            try:
                row["id"] = region_id(row["id"])
            except ValueError as exc:
                raise ValueError(f"Line {line}: {exc}") from None
            rows.append(row)
    return fields, rows


def read_values(path, catalog, value_column="value"):
    rows, zero_codes = [], []
    seen = set()
    _, sources = read_table(path, {"id", value_column})
    for line, source in enumerate(sources, 2):
        code = source["id"]
        if code not in catalog:
            raise ValueError(
                f"Line {line}: unknown id {code!r}; check the boundary feature IDs"
            )
        if code in seen:
            raise ValueError(f"Line {line}: duplicate id {code}")
        seen.add(code)
        try:
            value = float(source[value_column])
        except (TypeError, ValueError):
            raise ValueError(
                f"Line {line}: {code} needs a numeric {value_column}; blanks are not zero"
            ) from None
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Line {line}: {code} needs a finite non-negative value")
        if value == 0:
            zero_codes.append(code)
            continue
        base = catalog[code]
        status = (source.get("value_status") or "observed").strip()
        if status not in {
            "observed",
            "estimated",
            "interpolated",
            "carried_forward",
            "carried_backward",
        }:
            raise ValueError(f"Line {line}: unknown value_status {status!r}")
        explicit_group = (source.get("group") or "").strip()
        # A blank explicit parent disables the boundary metadata default.
        parent = (source.get("parent", base["parent"]) or "").strip().upper()
        rows.append(
            {
                "id": code,
                "value": value,
                "name": (source.get("name") or "").strip() or base["name"],
                "group": explicit_group or base["group"],
                "explicit_group": bool(explicit_group),
                "parent": parent,
                "value_status": status,
                "value_note": (source.get("value_note") or "").strip(),
            }
        )
    if not rows:
        raise ValueError("At least one strictly positive quantity is required")
    by_code = {r["id"]: r for r in rows}
    for row in rows:
        parent = row["parent"]
        if parent and parent not in catalog:
            raise ValueError(f"Unknown parent {parent!r} for {row['id']}")
        if parent == row["id"]:
            raise ValueError(f"Region {parent} cannot be its own parent")
        if parent and parent not in by_code:
            warnings.warn(
                f"{row['id']}: parent {parent} is absent; using its own geographic anchor"
            )
            row["parent"] = ""
        if row["parent"]:
            parent_row = by_code[row["parent"]]
            if parent_row["parent"]:
                raise ValueError(
                    "Parent links must be one level deep (no cycles or chains)"
                )
            if row["explicit_group"] and row["group"] != parent_row["group"]:
                raise ValueError(
                    f"{row['id']} and parent {row['parent']} must share a group"
                )
            row["group"] = parent_row["group"]
    rows.sort(key=lambda row: row["id"])
    total = math.fsum(r["value"] for r in rows)
    if not math.isfinite(total):
        raise ValueError("Sum of input values is not finite")
    return rows, {
        "omitted_region_ids": sorted(set(catalog) - seen),
        "zero_region_ids": zero_codes,
        "sum_of_values": total,
        "estimated_region_ids": [
            r["id"] for r in rows if r["value_status"] != "observed"
        ],
    }


def read_quantity_pairs(path, area_column, volume_column):
    """Read explicit A and B quantities; missing is never silently zero."""
    if area_column == volume_column:
        raise ValueError("Area and volume columns must be different")
    pairs = {}
    _, sources = read_table(path, {"id", area_column, volume_column})
    for line, source in enumerate(sources, 2):
        code = source["id"]
        if not code or code in pairs:
            raise ValueError(f"Line {line}: blank or duplicate id {code!r}")
        values = []
        for column in (area_column, volume_column):
            try:
                value = float(source[column])
            except (ValueError, TypeError):
                raise ValueError(
                    f"Line {line}: {code} needs a numeric {column}; blanks are not zero"
                ) from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"Line {line}: {column} must be finite and non-negative"
                )
            values.append(value)
        a, b = values
        if a == 0 and b > 0:
            raise ValueError(
                f"{code}: positive volume with zero area cannot be represented"
            )
        if a > 0 and not math.isfinite(b / a):
            raise ValueError(f"{code}: second / first ratio overflows")
        pairs[code] = (a, b)
    if not any(a > 0 for a, _ in pairs.values()):
        raise ValueError("At least one strictly positive area quantity is required")
    return pairs
