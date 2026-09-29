"""
Spielt eine Erstabgabe und beliebig viele Differenzabgaben chronologisch
sortiert in ein GeoPackage ein.

Nutzung:
    python run_import.py ausgabe.gpkg erstabgabe.xml diff_1.xml diff_2.xml diff_3.xml

Oder alle Dateien aus einem Ordner (werden automatisch anhand ihres
abgabeintervallEnde-Datums im Dateikopf sortiert - nicht anhand des
Dateinamens, da Dateinamen nicht zuverlaessig chronologisch sind):

    python run_import.py ausgabe.gpkg --verzeichnis /pfad/zu/nas_dateien/

WICHTIG: Die Erstabgabe muss erkennbar die ERSTE Lieferung sein (reine
Inserts). Das Skript sortiert alle Dateien nach abgabeintervallEnde und
verarbeitet sie in dieser Reihenfolge - die Erstabgabe muss also das
fruehste Datum haben.
"""

import argparse
import glob
import os
import sys

from parser import parse_delivery_metadata
from writer import import_delivery, finalize_geopackage, pruefe_chronologische_reihenfolge, pruefe_verfahren, pruefe_bereits_importiert


def sort_deliveries(paths):
    """Sortiert NAS-Dateien chronologisch anhand abgabeintervallEnde im Kopf,
    NICHT anhand des Dateinamens."""
    with_dates = []
    for p in paths:
        meta = parse_delivery_metadata(p)
        ende = meta.get("abgabeintervallEnde")
        if ende is None:
            print(f"WARNUNG: Kein abgabeintervallEnde in {p} gefunden - wird ans Ende sortiert")
            ende = "9999-99-99"
        with_dates.append((ende, p))
    with_dates.sort(key=lambda t: t[0])
    return with_dates


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gpkg", help="Pfad zum Ziel-GeoPackage (wird angelegt/erweitert)")
    ap.add_argument("dateien", nargs="*", help="NAS-XML-Dateien in beliebiger Reihenfolge (werden automatisch chronologisch sortiert)")
    ap.add_argument("--verzeichnis", help="Alternativ: Verzeichnis mit *.xml NAS-Dateien")
    ap.add_argument("--srs-id", type=int, default=None, help="EPSG-Code erzwingen statt automatisch zu erkennen")
    ap.add_argument("--force", action="store_true",
                     help="Reihenfolge-Warnungen ignorieren und trotzdem einspielen (nicht empfohlen)")
    args = ap.parse_args()

    paths = list(args.dateien)
    if args.verzeichnis:
        if "*" in args.verzeichnis or "?" in args.verzeichnis:
            # Nutzer hat direkt ein Glob-Muster angegeben (z.B. .../nas/*.xml)
            paths += sorted(glob.glob(args.verzeichnis))
        else:
            paths += sorted(glob.glob(os.path.join(args.verzeichnis, "*.xml")))

    if not paths:
        print("Keine Eingabedateien angegeben.")
        sys.exit(1)

    print(f"{len(paths)} Datei(en) gefunden, ermittle chronologische Reihenfolge...")
    ordered = sort_deliveries(paths)

    for ende, path in ordered:
        print(f"\n=== Einspielen: {os.path.basename(path)} (abgabeintervallEnde={ende}) ===")
        meta_vorab = parse_delivery_metadata(path)
        warnung_dup = pruefe_bereits_importiert(args.gpkg, meta_vorab, os.path.basename(path))
        if warnung_dup:
            print(f"  WARNUNG: {warnung_dup}")
            if not args.force:
                antwort = input("  Trotzdem einspielen? [j/N]: ").strip().lower()
                if antwort != "j":
                    print("  Übersprungen.")
                    continue
        warnung = pruefe_chronologische_reihenfolge(args.gpkg, meta_vorab)
        if warnung:
            print(f"  WARNUNG: {warnung}")
            if not args.force:
                antwort = input("  Trotzdem einspielen? [j/N]: ").strip().lower()
                if antwort != "j":
                    print("  Übersprungen.")
                    continue
        warnung_verfahren = pruefe_verfahren(args.gpkg, meta_vorab)
        if warnung_verfahren:
            print(f"  WARNUNG: {warnung_verfahren}")
            if not args.force:
                antwort = input("  Trotzdem einspielen? [j/N]: ").strip().lower()
                if antwort != "j":
                    print("  Übersprungen.")
                    continue
        counts, meta = import_delivery(args.gpkg, path, srs_id=args.srs_id)
        print(f"  Insert: {counts['insert']}, Replace: {counts['replace']}, Delete: {counts['delete']}")

    print("\n=== Finalisiere GeoPackage (Bounding Boxes, Attribut-/Geometrietabellen bereinigen) ===")
    finalize_geopackage(args.gpkg)
    print(f"\nFertig: {args.gpkg}")


if __name__ == "__main__":
    main()
