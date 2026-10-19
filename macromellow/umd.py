"""Fetch and parse the public HTML menu and nutrition labels at nutrition.umd.edu."""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo


BASE_URL = "https://nutrition.umd.edu/"
USER_AGENT = "MacroMellow/0.1 (+https://github.com/Salami2222/MacroMellow)"
HALLS = {
    "south": ("South Campus Dining Hall", "16"),
    "yahentamitsi": ("Yahentamitsi (The Y)", "19"),
    "251-north": ("251 North", "51"),
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
NUMBER = r"(\d+(?:\.\d+)?)"


def clean_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


class MenuParser(HTMLParser):
    """Read displayed items, their meal panes, and label URLs from UMD HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.meal_names: dict[str, str] = {}
        self.stations: dict[str, str] = {}
        self.items: list[dict[str, str]] = []
        self.capture: dict | None = None

    def _pane(self) -> str | None:
        for frame in reversed(self.stack):
            if frame.get("pane"):
                return frame["pane"]
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        pane = a.get("id") if tag == "div" and "tab-pane" in classes else None
        if tag not in VOID_TAGS:
            self.stack.append({"tag": tag, "pane": pane})
        if self.capture is not None:
            self.capture["depth"] += tag not in VOID_TAGS
            return
        current_pane = self._pane()
        if tag == "a" and a.get("role") == "tab" and a.get("aria-controls"):
            self.capture = {"kind": "meal", "pane": a["aria-controls"], "depth": 1, "text": []}
        elif current_pane and tag == "h3" and "card-title" in classes:
            self.capture = {"kind": "station", "pane": current_pane, "depth": 1, "text": []}
        elif current_pane and tag == "a" and "menu-item-name" in classes:
            self.capture = {"kind": "food", "pane": current_pane, "href": a.get("href", ""), "depth": 1, "text": []}

    def handle_data(self, data: str) -> None:
        if self.capture is not None:
            self.capture["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture is not None:
            self.capture["depth"] -= 1
            if self.capture["depth"] == 0:
                c = self.capture
                value = clean_text("".join(c["text"]))
                if c["kind"] == "meal":
                    self.meal_names[c["pane"]] = value
                elif c["kind"] == "station":
                    self.stations[c["pane"]] = value
                elif c["kind"] == "food" and value:
                    self.items.append({"name": value, "meal_pane": c["pane"], "station": self.stations.get(c["pane"], ""), "href": c["href"]})
                self.capture = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def foods(self) -> list[dict[str, str]]:
        return [
            {"name": item["name"], "meal": self.meal_names.get(item["meal_pane"], ""), "station": item["station"], "href": item["href"]}
            for item in self.items
        ]


class LabelParser(HTMLParser):
    """Read the per-serving values in the primary Nutrition Facts panel."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.capture: dict | None = None
        self.serving_parts: list[str] = []
        self.paragraphs: list[str] = []
        self.nutrients: list[str] = []
        self.title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((dict(attrs).get("class") or "").split())
        if self.capture is not None:
            self.capture["depth"] += tag not in VOID_TAGS
        elif tag == "h1":
            self.capture = {"kind": "title", "depth": 1, "text": []}
        elif tag == "div" and "nutfactsservsize" in classes:
            self.capture = {"kind": "serving", "depth": 1, "text": []}
        elif tag == "span" and "nutfactstopnutrient" in classes:
            self.capture = {"kind": "nutrient", "depth": 1, "text": []}
        elif tag == "p":
            self.capture = {"kind": "paragraph", "depth": 1, "text": []}

    def handle_data(self, data: str) -> None:
        if self.capture is not None:
            self.capture["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.capture is None:
            return
        self.capture["depth"] -= 1
        if self.capture["depth"] == 0:
            c = self.capture
            value = clean_text("".join(c["text"]))
            if c["kind"] == "title":
                self.title = value
            elif c["kind"] == "serving":
                self.serving_parts.append(value)
            elif c["kind"] == "nutrient":
                self.nutrients.append(value)
            elif c["kind"] == "paragraph":
                self.paragraphs.append(value)
            self.capture = None

    def values(self) -> dict:
        serving = None
        for before, after in zip(self.serving_parts, self.serving_parts[1:]):
            if before.lower() == "serving size":
                serving = after or None
                break

        calories = None
        for before, after in zip(self.paragraphs, self.paragraphs[1:]):
            if before.lower() == "calories per serving" and re.fullmatch(NUMBER, after):
                calories = float(after)
                break

        macros = {"fat_g": None, "carbohydrates_g": None, "protein_g": None}
        patterns = {
            "fat_g": rf"Total Fat\s*{NUMBER}\s*g",
            "carbohydrates_g": rf"Total Carbohydrate\.?\s*{NUMBER}\s*g",
            "protein_g": rf"Protein\s*{NUMBER}\s*g",
        }
        for nutrient in self.nutrients:
            for key, pattern in patterns.items():
                match = re.fullmatch(pattern, nutrient, re.IGNORECASE)
                if match and macros[key] is None:
                    macros[key] = float(match.group(1))
        return {"serving_size": serving, "calories": calories, **macros}


def parse_menu(html: str) -> list[dict[str, str]]:
    parser = MenuParser()
    parser.feed(html)
    return parser.foods()


def parse_label(html: str) -> dict:
    parser = LabelParser()
    parser.feed(html)
    return parser.values()


def check_robots() -> RobotFileParser | None:
    """A missing robots.txt allows access; other retrieval failures stop the run."""
    url = urljoin(BASE_URL, "robots.txt")
    try:
        with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=20) as response:
            if response.status != 200:
                raise RuntimeError(f"robots.txt returned HTTP {response.status}")
            lines = response.read(100_001).decode("utf-8", errors="replace").splitlines()
            if len("\n".join(lines)) > 100_000:
                raise RuntimeError("robots.txt exceeds size limit")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"Cannot verify robots.txt: HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot verify robots.txt: {exc.reason}") from exc
    parser = RobotFileParser(url)
    parser.parse(lines)
    return parser


def fetch_html(url: str, robots: RobotFileParser | None, max_bytes: int) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "nutrition.umd.edu":
        raise ValueError(f"Refusing non-UMD URL: {url}")
    if robots is not None and not robots.can_fetch(USER_AGENT, url):
        raise PermissionError(f"Disallowed by robots.txt: {url}")
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}), timeout=30) as response:
        final = urlparse(response.url)
        if final.scheme != "https" or final.hostname != "nutrition.umd.edu":
            raise ValueError(f"Unexpected redirect from UMD: {response.url}")
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError(f"Unexpected content type at {url}")
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError(f"Response too large at {url}")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def menu_url(hall_code: str, menu_date: date) -> str:
    return BASE_URL + "?" + urlencode({"locationNum": hall_code, "dtdate": f"{menu_date.month}/{menu_date.day}/{menu_date.year}"})


def label_url(href: str) -> str:
    """Encode UMD's occasionally unescaped serving multiplier in a label link."""
    absolute = urlsplit(urljoin(BASE_URL, href))
    query = urlencode(parse_qsl(absolute.query, keep_blank_values=True), quote_via=quote)
    return urlunsplit((absolute.scheme, absolute.netloc, absolute.path, query, absolute.fragment))


def retrieve(menu_date: date, hall_keys: list[str], delay: float = 0.25, max_labels: int | None = None) -> dict:
    robots = check_robots()
    timestamp = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    result = {"schema_version": 1, "source": "UMD Dining Services", "menu_date": menu_date.isoformat(), "retrieved_at": timestamp, "foods": [], "errors": []}
    label_cache: dict[str, dict | str] = {}
    request_count = 0

    def paced_fetch(url: str, size: int) -> str:
        nonlocal request_count
        if request_count:
            time.sleep(delay)
        request_count += 1
        return fetch_html(url, robots, size)

    for hall_key in hall_keys:
        hall_name, hall_code = HALLS[hall_key]
        source_menu_url = menu_url(hall_code, menu_date)
        try:
            menu_items = parse_menu(paced_fetch(source_menu_url, 3_000_000))
            if not menu_items:
                result["errors"].append({"dining_hall": hall_name, "url": source_menu_url, "error": "No menu items found for this date"})
                continue
        except (HTTPError, URLError, HTTPException, TimeoutError, ValueError, PermissionError) as exc:
            result["errors"].append({"dining_hall": hall_name, "url": source_menu_url, "error": str(exc)})
            if isinstance(exc, PermissionError) or (isinstance(exc, HTTPError) and exc.code in (403, 429)):
                break
            continue

        for item in menu_items:
            source_label_url = label_url(item["href"])
            food = {
                "dining_hall": hall_name,
                "menu_date": menu_date.isoformat(),
                "meal": item["meal"] or None,
                "station": item["station"] or None,
                "name": item["name"],
                "serving_size": None,
                "calories": None,
                "protein_g": None,
                "carbohydrates_g": None,
                "fat_g": None,
                "menu_url": source_menu_url,
                "source_url": source_label_url if item["href"] else None,
                "retrieved_at": None,
            }
            if not item["href"]:
                result["errors"].append({"dining_hall": hall_name, "food": item["name"], "url": source_menu_url, "error": "Missing nutrition label link"})
            elif source_label_url in label_cache:
                cached = label_cache[source_label_url]
                if isinstance(cached, dict):
                    food.update(cached)
                else:
                    result["errors"].append({"dining_hall": hall_name, "food": item["name"], "url": source_label_url, "error": cached})
            elif max_labels is not None and len(label_cache) >= max_labels:
                result["errors"].append({"dining_hall": hall_name, "food": item["name"], "url": source_label_url, "error": "Label skipped by --max-labels"})
            else:
                try:
                    label_html = paced_fetch(source_label_url, 200_000)
                    values = parse_label(label_html)
                    label_cache[source_label_url] = values
                    food.update(values)
                    if all(value is None for value in values.values()):
                        message = ("UMD states nutrition information is not available for this recipe"
                                   if "labelnotavailable" in label_html.lower() else "No nutrition fields found")
                        result["errors"].append({"dining_hall": hall_name, "food": item["name"], "url": source_label_url, "error": message})
                except (HTTPError, URLError, HTTPException, TimeoutError, ValueError, PermissionError) as exc:
                    label_cache[source_label_url] = str(exc)
                    result["errors"].append({"dining_hall": hall_name, "food": item["name"], "url": source_label_url, "error": str(exc)})
                    if isinstance(exc, PermissionError) or (isinstance(exc, HTTPError) and exc.code in (403, 429)):
                        food["retrieved_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
                        result["foods"].append(food)
                        return result
            food["retrieved_at"] = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
            result["foods"].append(food)
    return result


def save_json(result: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)


def search(path: Path, query: str, hall: str | None = None) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    foods = data.get("foods", [])
    needle = query.casefold()
    return [food for food in foods if needle in food["name"].casefold() and (hall is None or food["dining_hall"] == HALLS[hall][0])]
