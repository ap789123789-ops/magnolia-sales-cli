"""Print recent Magnolia property sales with matching King County parcel details.

Setup: python3 -m pip install -r requirements.txt
Run:   python3 magnolia_sales.py --days 30
       python3 magnolia_sales.py --days 14 --as-of 2026-10-01
       python3 magnolia_sales.py --days 30 --output sales.json
"""

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
import sys
from typing import Any

import requests


BOUNDARY_URL = (
    "https://services.arcgis.com/ZOyb2t4B0UYuYNYH/ArcGIS/rest/services/"
    "neighborhoods/FeatureServer/44/query"
)
SALES_URL = (
    "https://gismaps.kingcounty.gov/arcgis/rest/services/Property/"
    "KingCo_PropertyInfo/MapServer/3/query"
)
PARCEL_URL = (
    "https://services.arcgis.com/Ej0PsM5Aw677QF1W/arcgis/rest/services/"
    "PARCEL_ADDRESS_PUB_AREA_3069/FeatureServer/0/query"
)
SALES_FIELDS = (
    "PIN,address,SaleDate,SalePrice,ExciseTaxNum,buyername,Sellername,"
    "Property_Type,Principal_Use"
)
PARCEL_FIELDS = (
    "PIN,ADDR_FULL,LOTSQFT,APPRLNDVAL,APPR_IMPR,KCA_ZONING,PREUSE_DESC,LAT,LON"
)


def query_api(
    url: str,
    parameters: dict[str, str | int],
    post: bool = False,
) -> list[dict[str, Any]]:
    """Call an ArcGIS query endpoint and return its feature records.

    Args:
        url: Full URL of the layer's /query endpoint.
        parameters: ArcGIS options such as where, outFields, and f=json.
        post: If True, send options in a POST form body; otherwise use GET
            URL parameters. Both modes read records from the API.

    Returns:
        A list of feature dictionaries. A feature contains an attributes
        dictionary and, when requested, a geometry dictionary. An empty
        list means the query matched no records.

    Raises:
        requests.RequestException: The network request or HTTP status failed,
            or the response could not be decoded as JSON.
        ValueError: ArcGIS reported an error, returned unexpected data, or
            truncated the results at its record limit.
    """
    if post:
        response = requests.post(url, data=parameters, timeout=30)
    else:
        response = requests.get(url, params=parameters, timeout=30)
    response.raise_for_status()  # Raise HTTPError for HTTP 4xx or 5xx responses.
    payload = response.json()  # Decode the response body into Python objects.

    if not isinstance(payload, dict):
        raise ValueError("The API returned an unexpected response.")
    if "error" in payload:
        raise ValueError(f"ArcGIS: {payload['error'].get('message', 'request failed')}")
    if payload.get("exceededTransferLimit"):
        raise ValueError("API result limit reached. Try a shorter --days window.")
    features = payload.get("features")  # Read the key; return None if absent.
    if not isinstance(features, list):
        raise ValueError("The API response is missing its features list.")
    return features


def fetch_magnolia_geometry() -> dict[str, Any]:
    """Fetch the City of Seattle's Magnolia Community Reporting Area polygon.

    Args:
        None. The endpoint and Magnolia filter are defined in this script.

    Returns:
        An ArcGIS geometry dictionary with polygon rings (lists of
        longitude/latitude points in EPSG:4326), ready for the sales query.

    Raises:
        ValueError: Exactly one boundary was not found or its rings are missing.
        requests.RequestException: The API request failed. API response
            validation errors from query_api also propagate to the caller.
    """
    features = query_api(BOUNDARY_URL, {
        "where": "AREA_NAME = 'Magnolia' AND TYPE = 'CRA'",
        "outFields": "AREA_NAME",
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "json",
    })
    if len(features) != 1:
        raise ValueError("Expected exactly one Magnolia boundary.")
    geometry = features[0].get("geometry")
    if not isinstance(geometry, dict) or not geometry.get("rings"):
        raise ValueError("Magnolia boundary is missing its polygon rings.")
    return geometry


def date_window(days: int, as_of: date) -> tuple[date, date]:
    """Calculate a reporting window containing exactly days calendar dates.

    Args:
        days: Positive number of calendar dates to include.
        as_of: Last included date in the reporting window.

    Returns:
        A pair (inclusive_start, exclusive_end) of date objects. For example,
        30 days ending 2026-10-22 returns (2026-09-23, 2026-10-23).

    Raises:
        ValueError: days is not positive or the window exceeds supported dates.
    """
    if days <= 0:
        raise ValueError("--days must be a positive integer.")
    try:
        return as_of - timedelta(days=days - 1), as_of + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("The requested date window is outside supported dates.") from exc


def fetch_recent_sales(
    geometry: dict[str, Any], days: int, as_of: date,
) -> list[dict[str, Any]]:
    """Fetch positive-price sales intersecting a polygon during a date window.

    Args:
        geometry: ArcGIS polygon dictionary with rings in EPSG:4326,
            usually returned by fetch_magnolia_geometry.
        days: Positive number of calendar dates to include.
        as_of: Last included date in the reporting window.

    Returns:
        A list of sales dictionaries containing the nine SALES_FIELDS.
        SaleDate is converted from Unix milliseconds into a Python date;
        PIN remains a 10-digit string. Results are sorted newest first,
        then by address. Returns an empty list when no sales match.

    Raises:
        ValueError: The date window, PIN, price, or returned sale date is invalid.
        requests.RequestException: The API request failed. Other malformed
            response errors propagate to main for user-facing reporting.
    """
    start, end = date_window(days, as_of)
    features = query_api(SALES_URL, {
        "where": (
            f"SaleDate >= DATE '{start}' AND SaleDate < DATE '{end}' "
            "AND SalePrice > 0"
        ),
        "geometry": json.dumps(geometry),  # Encode the dictionary as JSON text.
        "geometryType": "esriGeometryPolygon",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": SALES_FIELDS,
        "orderByFields": "SaleDate DESC,address ASC",
        "returnGeometry": "false",
        "f": "json",
    }, post=True)

    sales = []
    for feature in features:
        sale = feature["attributes"].copy()
        pin = sale.get("PIN")
        if not isinstance(pin, str) or len(pin) != 10 or not pin.isdigit():
            raise ValueError("A sale record has an invalid parcel number (PIN).")
        sale["SaleDate"] = datetime.fromtimestamp(
            sale["SaleDate"] / 1000, tz=timezone.utc
        ).date()
        if not start <= sale["SaleDate"] < end:
            raise ValueError("The API returned a sale outside the requested date window.")
        if not math.isfinite(sale["SalePrice"]) or sale["SalePrice"] <= 0:
            raise ValueError(f"Invalid sale price for parcel {pin}.")
        sales.append(sale)

    # Python's sort is stable: order by address, then date newest first.
    sales.sort(key=lambda sale: sale.get("address") or "")
    sales.sort(key=lambda sale: sale["SaleDate"], reverse=True)
    return sales


def fetch_parcels(pins: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch parcel details and index them by parcel identification number.

    Args:
        pins: List of 10-digit PIN strings. Duplicates are removed before
            requesting records; leading zeros are preserved.

    Returns:
        A dictionary whose keys are PINs and whose values are parcel
        attributes containing the nine PARCEL_FIELDS. For example,
        {"2225039071": {"PIN": "2225039071", "LOTSQFT": 6400, ...}}.
        Missing parcels are omitted. An empty input returns {} without an
        API call. If duplicate PINs are returned, the last record wins.

    Raises:
        ValueError: An input PIN is invalid or API response validation fails.
        requests.RequestException: The API request failed.
    """
    if not pins:
        return {}
    unique_pins = sorted(set(pins))
    if any(len(pin) != 10 or not pin.isdigit() for pin in unique_pins):
        raise ValueError("Parcel numbers must contain exactly 10 digits.")
    quoted_pins = ",".join(f"'{pin}'" for pin in unique_pins)
    features = query_api(PARCEL_URL, {
        "where": f"PIN IN ({quoted_pins})",
        "outFields": PARCEL_FIELDS,
        "returnGeometry": "false",
        "f": "json",
    })
    return {feature["attributes"]["PIN"]: feature["attributes"] for feature in features}


def combine_records(
    sales: list[dict[str, Any]], parcels: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join each sale with its parcel details using the shared PIN.

    Args:
        sales: List of sales dictionaries returned by fetch_recent_sales.
        parcels: Dictionary mapping PIN strings to parcel dictionaries,
            as returned by fetch_parcels.

    Returns:
        A new list with one combined dictionary per sale, in the same order.
        Sales without a parcel match are retained. Sale values take priority
        if a key exists in both dictionaries. Inputs are not modified.
    """
    return [{**parcels.get(sale["PIN"], {}), **sale} for sale in sales]


def save_json(records: list[dict[str, Any]], filename: str) -> None:
    """Save combined records as readable JSON without changing the inputs.

    Args:
        records: Combined sales/parcel dictionaries from combine_records,
            with a Python date in each SaleDate field. An empty list is valid.
        filename: Output file path. Its parent directory must already exist;
            an existing file is overwritten.

    Returns:
        None. Writes a UTF-8 JSON list with full, unshortened field values.
        SaleDate values become YYYY-MM-DD strings; an empty list writes [].

    Raises:
        OSError: The output file could not be opened or written.
        TypeError: A record contains a value that JSON cannot serialize.
    """
    data = [
        {**record, "SaleDate": record["SaleDate"].isoformat()}
        for record in records
    ]
    with open(filename, "w", encoding="utf-8") as output_file:
        json.dump(data, output_file, indent=2, ensure_ascii=False)
        output_file.write("\n")


def format_value(value: Any, style: str = "text", width: int = 28) -> str:
    """Convert one record value into a printable table cell.

    Args:
        value: A field value, such as a string, number, date, or None.
        style: 'money' for dollar amounts, 'number' for comma-separated
            whole numbers, 'coordinate' for five decimal places, or 'text'.
        width: Maximum displayed text length, including a trailing ellipsis.
            Used for text only; numeric formats are not truncated.

    Returns:
        A formatted string. Missing or blank values become an em dash.
        Text is kept on one printable line and shortened when necessary.
        The original value is not modified.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return "—"
    if style == "money":
        return f"${value:,.0f}"
    if style == "number":
        return f"{value:,.0f}"
    if style == "coordinate":
        return f"{value:.5f}"
    # Keep untrusted API strings on one printable terminal line.
    text = " ".join("".join(char if char.isprintable() else " " for char in str(value)).split())
    return text if len(text) <= width else text[:width - 1] + "…"


def print_table(
    records: list[dict[str, Any]], columns: list[tuple[str, str, str, int]],
) -> None:
    """Print records as a table with headings and aligned columns.

    Args:
        records: Nonempty list of dictionaries providing the row values.
        columns: List of (heading, field_name, style, text_width) tuples.
            Example: ('PRICE', 'SalePrice', 'money', 16). Field names select
            values from each record; style and width go to format_value.

    Returns:
        None. Writes the formatted table to standard output. Column widths
        are calculated from the displayed headings and cell strings.
    """
    rows = [
        [format_value(record.get(field), style, width) for _, field, style, width in columns]
        for record in records
    ]
    headers = [column[0] for column in columns]
    widths = [max(len(header), *(len(row[index]) for row in rows))
              for index, header in enumerate(headers)]
    for row in [headers, ["-" * width for width in widths], *rows]:
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))


def print_report(
    records: list[dict[str, Any]],
    parcels: dict[str, dict[str, Any]],
    days: int,
    as_of: date,
) -> None:
    """Print the date window, match counts, and sales and parcel tables.

    Args:
        records: Combined sales/parcel dictionaries from combine_records.
        parcels: PIN-to-parcel lookup used to count matched sales.
        days: Positive number of dates in the reporting window.
        as_of: Last included date, used to label the report.

    Returns:
        None. Writes a report to standard output. With no records, prints
        a no-results message instead of tables. Does not modify the inputs.
    """
    start, _ = date_window(days, as_of)
    if not records:
        print(f"No Magnolia property sales found from {start} through {as_of}.")
        return

    matched = sum(record["PIN"] in parcels for record in records)
    print(f"Magnolia property sales — {start} through {as_of}")
    print(f"{len(records)} sales | {matched} parcel matches | {len(records) - matched} unmatched")
    print("\nSALES & PARTIES")
    print_table(records, [
        ("DATE", "SaleDate", "text", 10),
        ("PRICE", "SalePrice", "money", 16),
        ("EXCISE #", "ExciseTaxNum", "text", 15),
        ("ADDRESS", "address", "text", 28),
        ("BUYER", "buyername", "text", 22),
        ("SELLER", "Sellername", "text", 22),
        ("TYPE", "Property_Type", "text", 24),
        ("USE", "Principal_Use", "text", 24),
        ("PIN", "PIN", "text", 10),
    ])
    print("\nPARCEL DETAILS")
    print_table(records, [
        ("PIN", "PIN", "text", 10),
        ("ADDRESS", "ADDR_FULL", "text", 28),
        ("LOT SQFT", "LOTSQFT", "number", 12),
        ("LAND VALUE", "APPRLNDVAL", "money", 16),
        ("IMPROVEMENTS", "APPR_IMPR", "money", 16),
        ("ZONING", "KCA_ZONING", "text", 20),
        ("PRESENT USE", "PREUSE_DESC", "text", 24),
        ("LAT", "LAT", "coordinate", 12),
        ("LON", "LON", "coordinate", 12),
    ])
    print("\nBuyer/seller names describe the transaction; parcel details reflect the current dataset.")


def main(argv: list[str] | None = None) -> int:
    """Read CLI arguments, fetch/join API data, and print or export the report.

    Args:
        argv: Argument strings such as ['--days', '7'], excluding the script
            name. None reads arguments supplied at the command line.

    Returns:
        Exit code 0 for a successful report, empty results, or --help;
        exit code 1 for invalid arguments or a request/data/file error. Prints
        report/help text to standard output and errors to standard error.
        Also saves the combined records when --output FILE is provided.
    """
    parser = argparse.ArgumentParser(
        description="Print recent recorded property sales in Seattle's Magnolia neighborhood."
    )
    parser.add_argument("--days", type=int, default=30,
                        help="number of calendar dates to include; default: 30")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                        metavar="YYYY-MM-DD", help="final reporting date; default: today")
    parser.add_argument("--output", metavar="FILE",
                        help="also save full records as JSON (overwrites FILE)")
    try:
        # argparse normally uses exit code 2 for invalid arguments; use 1 consistently.
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return 0 if exc.code == 0 else 1
        date_window(args.days, args.as_of)
        geometry = fetch_magnolia_geometry()
        sales = fetch_recent_sales(geometry, args.days, args.as_of)
        parcels = fetch_parcels([sale["PIN"] for sale in sales])
        records = combine_records(sales, parcels)
        if args.output is not None:
            save_json(records, args.output)
        print_report(records, parcels, args.days, args.as_of)
        if args.output is not None:
            print(f"\nSaved JSON to {args.output}")
        return 0
    except (requests.RequestException, ValueError, KeyError, TypeError,
            AttributeError, OverflowError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
