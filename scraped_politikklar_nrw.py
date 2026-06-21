#!/usr/bin/env python3
"""
Scraper for the NRW Landtag parliamentary document search.

Paginates through:
  https://www.landtag.nrw.de/home/dokumente/dokumentensuche/parlamentsdokumente/aktuelle-dokumente.html
    ?formId=searchByArt&wp=<Wahlperiode>&dokTyp=<Dokumenttyp>&page=<N>

For every <li class="m-filterlist-documents__result-wrapper"> result it extracts:
  - drucksache    e.g. "18/20036"
  - titel         full title of the document
  - dokumenttyp   e.g. "Antrag"
  - ausgabedatum  e.g. "17.06.2026"
  - urheber       e.g. "SPD", in an array of strings
  - seiten        page count of the PDF (int, if present)
  - url           absolute URL to the PDF

and writes the collected list to a JSON file.

Usage:
  pip install requests beautifulsoup4 --break-system-packages   # if needed
  python3 nrw_landtag_scraper.py --wp 18 --doktyp 005 --output ergebnisse.json

Run a quick offline check of the parsing logic (no network calls) with:
  python3 nrw_landtag_scraper.py --selftest
"""

import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Comment

BASE_URL = "https://www.landtag.nrw.de"
SEARCH_PATH = "/home/dokumente/dokumentensuche/parlamentsdokumente/aktuelle-dokumente.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

RESULT_ITEM_CLASS = "m-filterlist-documents__result-wrapper"


def _clean_text(tag):
    """Return visible text of a tag, stripped of sr-only helper text, svg icons
    and the empty title-text <p>, with whitespace collapsed."""
    if tag is None:
        return None
    clone = BeautifulSoup(str(tag), "html.parser")
    for comment in clone.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for junk in clone.select(".sr-only, svg, p.e-document-result-item__title-text"):
        junk.decompose()
    text = clone.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_results(html):
    """Parse one search-results page and return a list of document dicts."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for li in soup.select(f"li.{RESULT_ITEM_CLASS}"):
        try:
            a_key = li.select_one("a.e-document-result-item__key")
            a_title = li.select_one("a.e-document-result-item__title")
            time_tag = li.select_one("time")
            categories = li.select("p.e-document-result-item__category")
            body_p = li.select_one("div.e-document-result-item__body p")

            drucksache = _clean_text(a_key)

            href = a_key.get("href") if a_key else None
            url = urljoin(BASE_URL, href) if href else None

            titel = _clean_text(a_title)

            # First category label is always "Drucksache"; the real document
            # type (Antrag, Antwort, Kleine Anfrage, ...) is the next one.
            dokumenttyp = None
            for cat in categories:
                txt = cat.get_text(strip=True)
                if txt and txt.lower() != "drucksache":
                    dokumenttyp = txt
                    break

            ausgabedatum = None
            if time_tag is not None:
                ausgabedatum = time_tag.get("datetime") or time_tag.get_text(strip=True)

            urheber = None
            seiten = None
            if body_p is not None:
                body_text = body_p.get_text(separator="\n")
                seiten_match = re.search(r"(\d+)\s*Seite", body_text)
                if seiten_match:
                    seiten = int(seiten_match.group(1))
                urheber_match = re.search(r"Urheber:\s*(.+)", body_text)
                if urheber_match:
                    # Split parties by comma and strip whitespace
                    urheber = [party.strip() for party in urheber_match.group(1).split(",")]

            items.append(
                {
                    "drucksache": drucksache,
                    "titel": titel,
                    "dokumenttyp": dokumenttyp,
                    "ausgabedatum": ausgabedatum,
                    "urheber": urheber,
                    "seiten": seiten,
                    "url": url,
                }
            )
        except Exception as exc:  # keep going even if one item is malformed
            print(f"  [warn] failed to parse one result item: {exc}", file=sys.stderr)
    return items


def fetch_page(session, wp, doktyp, page, timeout=20):
    params = {
        "formId": "searchByArt",
        "wp": wp,
        "dokTyp": doktyp,
        "page": page,
    }
    resp = session.get(BASE_URL + SEARCH_PATH, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def scrape(wp, doktyp, start_page=1, max_pages=None, delay=1.0):
    session = requests.Session()
    session.headers.update(HEADERS)

    all_results = []
    seen_drucksachen = set()
    page = start_page
    pages_done = 0

    while True:
        print(f"Fetching page {page} ...", file=sys.stderr)
        html = fetch_page(session, wp, doktyp, page)
        items = parse_results(html)

        if not items:
            print("No more results, stopping.", file=sys.stderr)
            break

        new_items = [it for it in items if it["drucksache"] not in seen_drucksachen]
        if not new_items:
            print("Page repeats previous results, stopping.", file=sys.stderr)
            break

        for it in new_items:
            if it["drucksache"]:
                seen_drucksachen.add(it["drucksache"])
        all_results.extend(new_items)

        pages_done += 1
        if max_pages and pages_done >= max_pages:
            print(f"Reached --max-pages limit ({max_pages}), stopping.", file=sys.stderr)
            break

        page += 1
        time.sleep(delay)

    return all_results


SAMPLE_HTML = """
<li class="m-filterlist-documents__result-wrapper">
  <div class="row justify-content-center">
    <div class="col-12 col-md-10 col-xl-8">
      <div class="m-filterlist-documents__result">
        <article class="e-document-result-item">
          <div class="row my-n3">
            <div class="col-4 col-md-3 py-3">
              <p class="e-document-result-item__category" style="margin:0">Drucksache</p>
              <a class="e-document-result-item__key" href="/portal/WWW/dokumentenarchiv/Dokument/MMD18-20036.pdf" target="_blank" rel="noopener noreferrer">
                18/20036<span class="sr-only"> ( externer Link, öffnet in neuem Tab , lädt eine PDF-Datei herunter ) </span> <!-- end include: Atom._linkAccessibleHelper -->
              </a>
              <div class="e-document-result-item__date">
                <p>Ausgabedatum:</p>
                <p><time datetime="17.06.2026">17.06.2026</time></p>
              </div>
            </div>
            <div class="col-8 col-md-3 py-3">
              <p class="e-document-result-item__category">Antrag</p>
            </div>
            <div class="col-12 col-md-6 py-3">
              <a class="e-document-result-item__title" href="/portal/WWW/dokumentenarchiv/Dokument/MMD18-20036.pdf" target="_blank" rel="noopener noreferrer">
                Einsetzung eines Untersuchungsausschusses Beispieltitel A
                <svg class="a-icon a-icon--external a-icon--Pdf e-document-result-item__title-icon" width="16" height="16">
                  <use xlink:href="/modules/landtag_templateSetR2020/images/svg-icons/icons-sprite.svg#Pdf" href="/modules/landtag_templateSetR2020/images/svg-icons/icons-sprite.svg#Pdf"></use>
                </svg> <!-- end include: Atom.asIconUsePdf -->
                <p class="e-document-result-item__title-text "></p>
                <!-- start include: Atom._linkAccessibleHelper --> <span class="sr-only"> ( externer Link, öffnet in neuem Tab , lädt eine PDF-Datei herunter ) </span> <!-- end include: Atom._linkAccessibleHelper -->
              </a>
              <div class="e-document-result-item__body">
                <p>19 Seite(n)<br>Urheber: SPD</p>
              </div>
            </div>
          </div>
        </article>
      </div>
    </div>
  </div>
</li>
<li class="m-filterlist-documents__result-wrapper">
  <div class="row justify-content-center">
    <div class="col-12 col-md-10 col-xl-8">
      <div class="m-filterlist-documents__result">
        <article class="e-document-result-item">
          <div class="row my-n3">
            <div class="col-4 col-md-3 py-3">
              <p class="e-document-result-item__category" style="margin:0">Drucksache</p>
              <a class="e-document-result-item__key" href="/portal/WWW/dokumentenarchiv/Dokument/MMD18-19798.pdf" target="_blank" rel="noopener noreferrer">
                18/19798<span class="sr-only"> ( externer Link, öffnet in neuem Tab , lädt eine PDF-Datei herunter ) </span>
              </a>
              <div class="e-document-result-item__date">
                <p>Ausgabedatum:</p>
                <p><time datetime="12.06.2026">12.06.2026</time></p>
              </div>
            </div>
            <div class="col-8 col-md-3 py-3">
              <p class="e-document-result-item__category">Antrag</p>
            </div>
            <div class="col-12 col-md-6 py-3">
              <a class="e-document-result-item__title" href="/portal/WWW/dokumentenarchiv/Dokument/MMD18-19798.pdf" target="_blank" rel="noopener noreferrer">
                Einsetzung eines Untersuchungsausschusses Beispieltitel B
                <svg class="a-icon a-icon--external a-icon--Pdf e-document-result-item__title-icon" width="16" height="16"></svg>
                <p class="e-document-result-item__title-text "></p>
                <span class="sr-only"> ( externer Link, öffnet in neuem Tab , lädt eine PDF-Datei herunter ) </span>
              </a>
              <div class="e-document-result-item__body">
                <p>20 Seite(n)<br>Urheber: SPD</p>
              </div>
            </div>
          </div>
        </article>
      </div>
    </div>
  </div>
</li>
"""


def selftest():
    results = parse_results(SAMPLE_HTML)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    assert len(results) == 2
    assert results[0]["drucksache"] == "18/20036"
    assert results[0]["dokumenttyp"] == "Antrag"
    assert results[0]["ausgabedatum"] == "17.06.2026"
    assert results[0]["urheber"] == ["SPD"]
    assert results[0]["seiten"] == 19
    assert results[0]["url"] == (
        "https://www.landtag.nrw.de/portal/WWW/dokumentenarchiv/Dokument/MMD18-20036.pdf"
    )
    assert "Beispieltitel A" in results[0]["titel"]
    print("\nSelf-test passed.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wp", default="18", help="Wahlperiode, e.g. 18")
    parser.add_argument("--doktyp", default="005", help="Dokumenttyp-Code, e.g. 005 = Antrag")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=None, help="Safety cap on number of pages to fetch")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between page requests")
    parser.add_argument("--output", default="ergebnisse.json")
    parser.add_argument("--selftest", action="store_true", help="Run offline parser check and exit")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    results = scrape(
        wp=args.wp,
        doktyp=args.doktyp,
        start_page=args.start_page,
        max_pages=args.max_pages,
        delay=args.delay,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(results)} documents to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
