# Magnolia Sales

Find recent property sales in Seattle’s Magnolia neighborhood.

## Setup

Use Python 3.10 or newer. From the project folder, create and activate a virtual
environment (macOS/Linux):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

The virtual environment keeps this project's installed packages separate from
other projects. Activate it again whenever you open a new terminal to work here.

`requirements.txt` lists the project's external packages. Its entry
`requests>=2.32,<3` means Requests version 2.32 or newer, but below version 3.
The script uses Requests to call the public APIs; JSON support is built into Python.

## Usage

Print recent sales as tables:

```bash
python magnolia_sales.py --days 30
```

Optionally save the combined sales and parcel records to a JSON file too:

```bash
python magnolia_sales.py --days 30 --output sales.json
```

The tables still print. The JSON file contains full field values (including names
shortened in the tables), dates such as `"2026-10-23"`, and PINs stored as strings
to preserve leading zeros. If there are no matching sales, it contains `[]`.
An existing output file is overwritten; its parent directory must already exist.

Use `python magnolia_sales.py --help` to see all options.

When finished, leave the virtual environment:

```bash
deactivate
```

## Data sources

Seattle and King County public APIs.
