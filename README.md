# MacroMellow: UMD nutrition retrieval prototype

This Python prototype retrieves current menu items and per-serving nutrition facts from the [official UMD Dining nutrition site](https://nutrition.umd.edu/). It covers South Campus Dining Hall, Yahentamitsi (The Y), and 251 North. It does not estimate missing values.

## Run

Python 3.10+ is required. There are no third-party Python dependencies.

```bash
python3 -m macromellow fetch --hall all --output data/latest.json
python3 -m macromellow search "chicken" --file data/latest.json
python3 -m macromellow search "tofu" --file data/latest.json --hall yahentamitsi
```

Omit `--date` to use today's date in Maryland. `--hall` accepts `all`, `south`, `yahentamitsi`, or `251-north`. The default output is `data/umd-YYYY-MM-DD.json`; use `--output` to choose another path. A full fetch follows all linked nutrition labels and may take several minutes. For a quick connectivity check, `--max-labels 3` fetches all requested menus but only three unique labels; skipped labels remain `null` and are recorded as errors. The command returns exit status 2 when the saved result has any errors, and 1 if it cannot start (for example, robots.txt cannot be checked).

Search reads a saved JSON file without making network requests. It displays matching food names, hall, date, meal, serving size, calories, protein, carbohydrates, fat, and the official label URL. Unknown values display as `unknown`.

Run parser checks with:

```bash
python3 -m unittest discover -s tests -v
```

## Data source and method

UMD's public menu page has an ordinary GET form with `locationNum` and `dtdate`. Its [public JavaScript](https://nutrition.umd.edu/js/custom.js) uses those fields to change the displayed menu and offers a rolling seven-day date selector. The form lists hall IDs 16 (South Campus), 19 (Yahentamitsi), and 51 (251 North). Each menu item links to an individual `label.aspx?RecNumAndPort=...` page. The prototype reads the menu HTML, follows those official links, and extracts the primary Nutrition Facts panel's serving size, calories per serving, total fat, total carbohydrate, and protein. It stores the menu and label URLs with every item. A repeated label URL is fetched only once per run.

I found no publicly documented nutrition API on the official site during the initial investigation (October 19, 2026). This prototype uses the site's public HTML; it does not use a private endpoint or bypass an access restriction. The site's `/robots.txt` returned HTTP 404 when checked. At run time, the program checks it again: explicit disallow rules are honored, while unexpected failures stop retrieval. Requests are sequential with at least 0.25 seconds between them; HTTP 403 or 429 stops the run. The default delay can be increased with `--delay`.

## JSON format

The JSON file contains `schema_version`, `source`, `menu_date`, `retrieved_at`, `foods`, and `errors`. Each `foods` entry contains `dining_hall`, `menu_date`, `meal`, `station`, `name`, `serving_size`, `calories`, `protein_g`, `carbohydrates_g`, `fat_g`, `menu_url`, `source_url`, and `retrieved_at`. Nutrients are **per listed serving**, not per meal. A food can appear more than once if UMD lists it at multiple meals or stations. Numeric fields and serving size are `null` when unavailable; `errors` records inaccessible pages and parsing failures. Saved data is excluded from Git because menus change and a full scrape is relatively large.

## Limits

- UMD's date picker offers today plus six days. Other dates may have no menu; the program reports an empty menu instead of assuming one.
- UMD notes that its nutritional analysis is approximate and can vary with ingredients and preparation. See an [official label](https://nutrition.umd.edu/label.aspx?RecNumAndPort=119370%2A1).
- The parser depends on UMD's current HTML structure. If UMD changes it, fields may become `null`; whole-label parsing failures appear in `errors`. Verify against the site before using the data in another product.
- A partial file may be saved when a request fails. Inspect `errors` before treating a dataset as complete.
- On October 19, 2026, six distinct official labels explicitly said nutrition information was unavailable. They account for 14 menu appearances with `null` nutrition fields in the saved test dataset.
