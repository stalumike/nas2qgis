# NAS2QGIS

Importiert NAS/ALKIS-Lieferungen (Erst- und Differenzabgaben, GID 7.x) in ein
**historisiertes GeoPackage** und ermöglicht:

- den visuellen Vergleich des Datenbestands zwischen zwei beliebigen
  Zeitpunkten – inklusive der tatsächlich veränderten Fläche (nicht nur
  "dieses Flurstück hat sich geändert", sondern "genau hier") und aller
  geänderten Attributwerte
- die automatische Ermittlung, welche Flurstücke zum eigenen
  Bodenordnungs-/Bauraumverfahren gehören (`beteiligt` /
  `nebenbeteiligt` / `nicht beteiligt`), auch wenn eine Lieferung mehrere
  BRB-Objekte benachbarter Verfahren enthält

Besteht aus einer reinen Python-Bibliothek (keine externen Abhängigkeiten,
läuft mit jedem Python 3 – auch ohne QGIS) und einem QGIS-Plugin
(**NAS2QGIS**), das diese Bibliothek als Menüoberfläche nutzt.

---

## Inhalt

- [Architektur-Überblick](#architektur-überblick)
- [Das Historisierungsmodell](#das-historisierungsmodell)
- [Bibliothek: parser.py](#bibliothek-parserpy)
- [Bibliothek: writer.py](#bibliothek-writerpy)
- [QGIS-Plugin](#qgis-plugin)
- [Beteiligtenstatus (BRB-Zugehörigkeit)](#beteiligtenstatus-brb-zugehörigkeit)
- [Kommandozeilen-Werkzeug: run_import.py](#kommandozeilen-werkzeug-run_importpy)
- [Release bauen: build_release.bat](#release-bauen-build_releasebat)
- [Nutzung: Schritt für Schritt](#nutzung-schritt-für-schritt)
- [Bekannte Einschränkungen](#bekannte-einschränkungen)
- [Dateiübersicht](#dateiübersicht)

---

## Architektur-Überblick

```
NAS-XML-Dateien (Erstabgabe + Differenzabgaben)
        │
        ▼
   parser.py         liest NAS-XML, liefert strukturierte NASFeature-Objekte
        │             (Insert/Replace/Delete, Geometrie, Attribute, Zeitstempel)
        ▼
   writer.py         schreibt historisiert in ein GeoPackage (reines sqlite3,
        │             kein GDAL/Fiona nötig)
        ▼
  bestand.gpkg        ein GeoPackage mit einer Tabelle je Objektart,
        │             jede Objektversion mit gueltig_von/gueltig_bis
        ▼
 beteiligung.py        (nur im Plugin) ermittelt je Flurstück den
        │              Beteiligtenstatus zum eigenen Verfahrensgebiet
        ▼
  QGIS-Plugin          Import-Dialog (Datei-Auswahl statt Kommandozeile) +
  (nas2qgis)            Vergleichs-Dialog (Zustand A/B, Differenzfläche,
                         Attributänderungen)
```

`parser.py` und `writer.py` haben **keine externen Abhängigkeiten** (nur
Python-Standardbibliothek: `xml.etree.ElementTree`, `sqlite3`, `struct`,
`math`). Das war eine bewusste Entscheidung, damit das QGIS-Plugin ohne
zusätzliche Installation (z. B. `lxml`, GDAL-Python-Bindings) läuft – es
nutzt einfach das Python, das in QGIS bereits mitgeliefert wird.
`beteiligung.py` ist die einzige Ausnahme: braucht PyQGIS (für robuste
Geometrie-Operationen) und läuft daher nur innerhalb des Plugins, nicht im
CLI-Tool.

---

## Das Historisierungsmodell

ALKIS/NAS liefert Differenzabgaben als Folge von `Insert`/`Replace`/`Delete`-
Sätzen, die immer nur den **aktuellen Stand** beschreiben. Wenn man diese
Sätze naiv einspielt (alte Version überschreiben), verliert man die
Möglichkeit nachzuvollziehen, wie sich z. B. eine Flurstücksgrenze über die
Zeit verschoben hat.

Deshalb historisiert `writer.py` nach dem **SCD2-Muster** (Slowly Changing
Dimension Typ 2): Jede Objektversion bekommt eine eigene Zeile mit zwei
Zeitstempel-Spalten:

| Spalte | Bedeutung |
|---|---|
| `oid` | stabile ADV-OID des Objekts (bleibt über alle Versionen gleich) |
| `gueltig_von` | Zeitpunkt, ab dem genau diese Version gilt (`lebenszeitintervall/beginnt` aus der NAS-Datei) |
| `gueltig_bis` | `NULL` = aktuell gültig, sonst Zeitpunkt der Ablösung |
| `geom` | Geometrie dieser Version (falls die Objektart eine hat) |
| ... | fachliche Attribute |

**Ablauf beim Einspielen einer Lieferung:**

- **Insert** → neue Zeile, `gueltig_von = beginnt`, `gueltig_bis = NULL`.
  Sonderfälle (siehe unten): idempotent bei Wiederholung, implizites Replace
  bei geänderter OID mit neuem `beginnt`.
- **Replace** → offene Zeile (`gueltig_bis IS NULL`) mit dieser OID wird
  geschlossen (`gueltig_bis = beginnt` der neuen Version), danach neue Zeile
  eingefügt.
- **Delete** → offene Zeile wird geschlossen (`gueltig_bis` = Datum aus dem
  Lieferungskopf, `abgabeintervallEnde`), keine neue Zeile.

**Zustand zu einem beliebigen Zeitpunkt X abfragen:**

```sql
SELECT * FROM AX_Flurstueck
WHERE gueltig_von <= 'X' AND (gueltig_bis IS NULL OR gueltig_bis > 'X')
```

**Wichtig – Reihenfolge:** Lieferungen müssen **chronologisch** eingespielt
werden (Erstabgabe zuerst, danach die Differenzabgaben in der richtigen
Reihenfolge), sonst ergeben die offenen/geschlossenen Intervalle keinen
Sinn. Sowohl `run_import.py` als auch der Import-Dialog im Plugin sortieren
automatisch anhand des `abgabeintervallEnde`-Datums im Dateikopf – die
Reihenfolge der übergebenen Dateien spielt keine Rolle.

### Sonderfälle, die reale NAS-Lieferungen tatsächlich enthalten

Diese Fälle wurden nicht theoretisch hergeleitet, sondern beim Testen mit
echten Lieferungen gefunden und gezielt abgesichert:

1. **Kontextobjekte werden erneut als Insert mitgeliefert**, obwohl sie
   inhaltlich unverändert sind (gleiche OID, gleiches `beginnt`). Wird
   erkannt und übersprungen (kein Duplikat).
2. **Ein Objekt wird nach einem vollen Re-Export erneut als Insert
   geliefert**, hat sich aber zwischenzeitlich tatsächlich geändert (gleiche
   OID, anderes `beginnt`), statt korrekt als `Replace` zu kommen. Ohne
   Sonderbehandlung würden zwei gleichzeitig "offene" Zeilen für dieselbe OID
   entstehen (das Objekt erscheint doppelt/überlappend auf der Karte). Wird
   jetzt wie ein Replace behandelt: alte Zeile schließen, neue öffnen.
   (Ein solcher Fall trat tatsächlich in den Testdaten auf: Flurstück
   `DEMVAL71Z000021M`.)
3. **Manche Fachobjekte tragen selbst keine Geometrie** (z. B.
   `AX_Grenzpunkt`, `AX_BesondererGebaeudepunkt`) – die Position steckt
   stattdessen in einem separaten Punktort-Objekt (`AX_PunktortAG/AU/TA`),
   das per `istTeilVon` auf die OID des Fachobjekts zurückverweist. Der
   Parser löst das automatisch auf und übernimmt die Punktort-Geometrie für
   das Fachobjekt.
4. **Grenzlinien werden nicht als eine durchgehende `posList` geliefert**,
   sondern als mehrere kurze `LineStringSegment`-Abschnitte
   (`gml:curveMember`), die zu einem Ring zusammengesetzt werden müssen.
   Auch Referenzen auf gemeinsame Grenzsegmente per `xlink:href` (statt
   Inline-Definition) werden unterstützt.
5. **Kreisbogenflurstücke**: Grenzabschnitte können statt als gerade Linie
   auch als Kreisbogen (`gml:Arc`/`gml:ArcString`, definiert über Start-,
   Zwischen- und Endpunkt) geliefert werden - z. B. bei Grundstücken an
   Straßenkurven/-kreiseln. Da GeoPackage/WKB keine echten Kreisbögen kennt,
   werden diese automatisch als Punktfolge angenähert (Kreis durch drei
   Punkte berechnen, Bogenrichtung bestimmen, in kurze Segmente
   interpolieren). Ohne diese Behandlung bekamen betroffene Flurstücke eine
   unvollständige/kaputte Geometrie und fielen z. B. beim
   [Beteiligtenstatus](#beteiligtenstatus-brb-zugehörigkeit) fälschlich raus
   (an echten Daten verifiziert: 4 Kreisbogenflurstücke, die nach dem Fix
   korrekt erkannt wurden).

---

## Bibliothek: parser.py

Liest eine NAS-XML-Datei (`AX_NutzerbezogeneBestandsdatenaktualisierung_NBA`
mit `wfs:Transaction`) und liefert je Objekt ein `NASFeature`:

```python
@dataclass
class NASFeature:
    action: str            # "insert" | "replace" | "delete"
    objektart: str          # z.B. "AX_Flurstueck"
    oid: str | None         # stabile ADV-OID (ohne "urn:adv:oid:"-Praefix)
    gml_id: str | None      # technische gml:id (bei Replace mit Zeitstempel-Suffix!)
    beginnt: str | None     # lebenszeitintervall/beginnt
    endet: str | None       # lebenszeitintervall/endet (i.d.R. nicht befuellt)
    attributes: dict
    geometry_wkt: str | None
```

Wichtige Funktionen:

- `parse_nas_file(path)` – Generator über alle Insert/Replace/Delete-Sätze
- `parse_delivery_metadata(path)` – liest Kopfdaten (`antragsnummer`,
  `abgabeintervallBeginn/Ende`, `profilkennung`, `crs`)

**Geometrie-Rekonstruktion:** Flächen (`MultiSurface`/`Surface`/`Polygon`)
werden aus `gml:PolygonPatch` → `exterior`/`interior` → `Ring` →
`curveMember` → `Curve` → Segmenten zusammengesetzt. Unterstützte
Segmenttypen: `LineStringSegment` (gerade Strecken) sowie `Arc`/`ArcString`
(Kreisbögen, werden als Punktfolge interpoliert). Gemeinsame Grenzsegmente,
die per `xlink:href` referenziert statt inline definiert sind, werden
aufgelöst.

**Codelisten-Referenzen** (z. B. `<anlass xlink:title="Teilung"
xlink:href=".../AA_Anlassart/060200"/>`) werden in drei Attribut-Spalten
aufgeschlüsselt: `<feld>` (volle URL, wie bisher), `<feld>_text` (Klartext
aus `xlink:title`), `<feld>_code` (Codenummer aus der URL). Gilt generisch
für jedes Feld mit diesem Muster, nicht nur `anlass`.

**Unterstützt aktuell nur GID 7.x.** GID 6-Lieferungen haben eine andere
Namespace-/Schemastruktur und werden derzeit **nicht** erkannt (Fehler beim
Lesen der Kopfdaten/CRS).

---

## Bibliothek: writer.py

Schreibt `NASFeature`-Objekte historisiert in ein GeoPackage – ohne GDAL,
Fiona oder PyQGIS, nur mit `sqlite3` und manuell gebautem
GeoPackage-Binärformat (WKT → WKB → `GeoPackageBinaryHeader`).

### Wichtigste Funktionen

```python
import_delivery(gpkg_path, nas_path, srs_id=None) -> (counts, meta)
```
Spielt **eine** NAS-Datei ein. `srs_id=None` (Standard): CRS wird
automatisch aus dem `<crs>`-Element im Dateikopf erkannt (ETRS89_UTM32/33,
DE_DHDN_3GK2–5). Bei unbekanntem CRS: Fehlermeldung mit Hinweis, `srs_id`
manuell zu setzen. Schreibt außerdem einen Eintrag in die Tabelle
`nas_lieferungen` (Dateiname, `abgabeintervallBeginn/Ende`,
Importzeitpunkt) - daraus speist sich im Plugin die Auswahlliste der
Vergleichszeitpunkte, statt dass man ein freies Datum raten müsste.

```python
finalize_geopackage(gpkg_path)
```
**Einmalig am Ende** aufrufen, nachdem *alle* Lieferungen eingespielt sind
(nicht nach jeder einzelnen Datei!):
- Tabellen, die durchgehend nie eine Geometrie hatten, werden zu sauberen
  Attributtabellen (`data_type='attributes'`) umgebaut – ohne das verletzt
  die Datei die GeoPackage-Spezifikation und QGIS kann beim Öffnen mit
  kryptischen Fehlern abbrechen ("Vector too long" o. ä.).
- Bounding-Box-Extents werden für alle Geometrietabellen berechnet.

Wird eine bereits finalisierte Datei später doch wieder mit Geometrie für
eine vorher "leere" Objektart beliefert, wird die `geom`-Spalte automatisch
wieder ergänzt und die Tabelle zurück zu `data_type='features'` gestuft –
das GeoPackage bleibt also auch bei mehrfachem
Import→Finalize→Import-Zyklus konsistent.

### GeoPackage-Aufbau je Objektart-Tabelle

| Spalte | Typ | Bedeutung |
|---|---|---|
| `fid` | INTEGER PK | interner GeoPackage-Zeilenzähler (**keine** fachliche Bedeutung) |
| `oid` | TEXT, indiziert | stabile ADV-OID |
| `gueltig_von` / `gueltig_bis` | TEXT | siehe Historisierungsmodell |
| `geom` | BLOB | nur falls die Objektart überhaupt Geometrie hat |
| ... | TEXT | alle in der NAS-Datei vorkommenden Attribute, Spalten werden dynamisch per `ALTER TABLE` ergänzt |

---

## QGIS-Plugin

Ordner `nas2qgis/`, Menü **Erweiterungen → NAS2QGIS**.

### NAS-Daten importieren...
(`import_dialog.py`)

- Ziel-GeoPackage wählen (neu oder bestehend)
- Beliebig viele NAS-Dateien per Datei-Dialog hinzufügen (Reihenfolge egal,
  wird automatisch anhand `abgabeintervallEnde` sortiert)
- "Importieren" spielt alle Dateien der Reihe nach ein, ruft am Ende
  automatisch `finalize_geopackage()` und danach
  `berechne_beteiligtenstatus()` auf (siehe unten); Protokoll läuft im
  Fenster mit
- "GeoPackage in QGIS laden" öffnet eine Checkbox-Liste aller
  Objektart-Layer (Vorauswahl über `STANDARD_LAYER_AUSWAHL` in
  `import_dialog.py` konfigurierbar, aktuell `AX_Flurstueck` +
  `AX_Gebaeude`) und lädt die ausgewählten mit einer Standard-Stilisierung
  (`styling.py`)

### Historie vergleichen...
(`vergleich_dialog.py`)

- Layer wählen (nur Layer mit Feld `gueltig_von` werden angeboten)
- Zeitpunkt A / B aus einer Liste der **tatsächlich importierten
  Lieferungen** wählen (aus `nas_lieferungen`, nicht aus tausenden
  Einzelobjekt-Zeitstempeln – die wären als Auswahlliste unbrauchbar lang)
- Ergebnis: vier neue Layer im Projekt
  - **Zustand A** / **Zustand B** – vollständiger Bestand zum jeweiligen
    Zeitpunkt
  - **Unterschiede** – nur veränderte Objekte, Feld `vergleichsstatus`
    (`neu` / `entfernt` / `geometrie_geaendert` / `attribute_geaendert` /
    `geometrie_und_attribute_geaendert`). Bei Geometrieänderungen wird
    **nicht** die komplette Fläche angezeigt, sondern nur die tatsächlich
    veränderte Stelle (symmetrische Differenz alt↔neu)
  - **Attributänderungen** – eine Zeile je geändertem Feld
    (`oid`, `feld`, `alter_wert`, `neuer_wert`), öffnet sich automatisch als
    QGIS-Attributtabelle (dort sortier-/filterbar, per Knopf als CSV
    exportierbar). Rein strukturelle Felder (`fid`, `oid`, `gueltig_von`,
    `gueltig_bis`) werden dabei ignoriert.

### styling.py

Zentrale Stelle für die Standard-Stilisierung neu geladener Layer: Flächen
ohne Füllung (nur Kontur), damit sich beim gleichzeitigen Laden vieler
Objektarten nichts gegenseitig überdeckt.

- `FARBPALETTE` – rotierende Farbliste für Objektarten ohne festen Stil
- `LAYER_STILE` – Dictionary für feste Farbe/Strichstärke je Objektart:
  ```python
  LAYER_STILE = {
      "AX_Flurstueck": {"farbe": "35,35,35", "breite": 0.6},
      "AX_Gebaeude": {"farbe": "227,26,28", "breite": 0.8},
  }
  ```
  `farbe`: `"r,g,b"` (Alpha wird automatisch auf 255 ergänzt), `breite`:
  Linienbreite/Punktgröße in mm. Objektarten ohne Eintrag bekommen
  automatisch die nächste Palettenfarbe.

---

## Beteiligtenstatus (BRB-Zugehörigkeit)

(`beteiligung.py`, läuft automatisch nach `finalize_geopackage()` im
Import-Dialog)

**Problem:** Manche NBA-Lieferungen enthalten mehrere
`AX_BauRaumOderBodenordnungsrecht`-Objekte (BRB), weil angrenzende
Verfahren mitgeliefert werden. Ein Textabgleich über den Lieferungskopf
(`antragsnummer` o. ä.) ist **nicht zuverlässig**, da nicht alle
Katasterämter den Kopf gleich aufbauen (an echten Testdaten geprüft: schon
zwischen zwei Gemarkungen unterscheidet sich, wie/ob das BRB überhaupt
`name`/`bezeichnung`-Felder trägt).

**Lösung:** Das BRB mit den **meisten aktuell überschneidenden
Flurstücken** gilt als das eigene Verfahrensgebiet – in der Praxis werden
NBA-Daten ohnehin nicht aus zwei verschiedenen eigenen Verfahren gemeinsam
eingelesen, daher ist dieses Kriterium robust genug.

Jede `AX_Flurstueck`-Zeile (auch historische Versionen) bekommt eine neue
Spalte `beteiligtenstatus`:

| Wert | Bedeutung |
|---|---|
| `beteiligt` | Geometrie überschneidet das Verfahrens-BRB flächig |
| `nebenbeteiligt` | berührt das BRB nur (angrenzend, keine Flächenüberschneidung) |
| `nicht beteiligt` | alles andere |

**An echten Daten verifiziert:** Abgleich gegen eine aus LEFIS exportierte
Referenzliste (1212 Flurstücke) ergab nach Behebung des
Kreisbogen-Problems (siehe oben) exakte Übereinstimmung.

### Robustheit gegenüber GEOS-Abstürzen

Räumliche Prüfungen (`intersects`/`touches`) auf topologisch ungewöhnlichen
Geometrien können in der aktuell eingesetzten QGIS-Version (3.40.12, GEOS
mit `RelateNG`-Algorithmus) zu einem **nativen Absturz** führen (Windows
"access violation" - lässt sich in Python **nicht** per try/except
abfangen). Dagegen abgesichert über:

1. Jede Geometrie wird vor jeder Prüfung über `geom.buffer(0, 8)` komplett
   neu aufgebaut (robusterer, älterer GEOS-Codepfad als `intersects()`
   direkt).
2. Vor jedem `intersects()`-Aufruf wird zuerst ein reiner
   Bounding-Box-Vergleich gemacht (kein GEOS, kann nicht abstürzen) - nur
   bei Überlappung wird überhaupt die genaue (riskantere) Prüfung
   ausgeführt.

Falls trotzdem wieder ein Absturz auftritt: Das wäre ein Hinweis auf einen
tieferliegenden GEOS-Bug in dieser QGIS-Version, der sich nur durch
komplett Verzicht auf `intersects()`/`touches()` (Ersatz durch reinen
Bounding-Box-Vergleich, auf Kosten der Genauigkeit bei unregelmäßig
geformten Verfahrensgebieten) umgehen ließe.

---

## Kommandozeilen-Werkzeug: run_import.py

Macht dasselbe wie der Import-Dialog, aber ohne QGIS – für
Automatisierung/Batch-Verarbeitung außerhalb der QGIS-Oberfläche. Enthält
**keinen** Beteiligtenstatus-Schritt (der braucht PyQGIS).

```bash
python run_import.py bestand.gpkg erstabgabe.xml diff_1.xml diff_2.xml
python run_import.py bestand.gpkg --verzeichnis /pfad/zu/nas_dateien/
python run_import.py bestand.gpkg --verzeichnis "/pfad/zu/nas_dateien/*.xml"
```

Sortiert automatisch chronologisch, ruft `finalize_geopackage()` am Ende
auf.

---

## Release bauen: build_release.bat

Windows-Batch-Skript (angelehnt an ein bestehendes internes Buildscript für
ein anderes Plugin), liegt im Repo-Root neben dem Plugin-Ordner:

```
O:\STALUMSAbt3\LEFIS-Administration\Code\NAS2QGIS\
├── build_release.bat
└── NAS2QGIS\              (Plugin-Ordner: metadata.txt, __init__.py, ...)
```

Ablauf beim Ausführen:
1. Aktuelle Version aus `metadata.txt` lesen, neue Version abfragen
2. `metadata.txt` aktualisieren
3. `plugins.xml` (Repository-Manifest) neu schreiben
4. `__pycache__` im Plugin-Ordner löschen, ZIP erstellen (versioniert,
   fürs Archiv: `nas2qgis_<version>.zip`)
5. Feste `nas2qgis.zip` aktualisieren (die, auf die `plugins.xml`
   verweist – QGIS-Repositories brauchen einen stabilen Download-Link)

Anders als beim LEFIS-Vorbild: **keine** Umgebungsauswahl (Test/Prod) und
**keine** `config.ini`-Logik, da NAS2QGIS keine Datenbank-Zielumgebung
kennt – es gibt nur ein Release-Ziel.

---

## Nutzung: Schritt für Schritt

1. Plugin installieren: **Erweiterungen → Erweiterungen verwalten und
   installieren → Aus ZIP installieren** → `nas2qgis.zip` wählen
2. **Erweiterungen → NAS2QGIS → NAS-Daten importieren...**
   - Ziel-GeoPackage wählen
   - Erstabgabe + alle vorhandenen Differenzabgaben hinzufügen
   - Importieren (inkl. automatischer Beteiligtenstatus-Ermittlung)
3. "GeoPackage in QGIS laden" → gewünschte Objektarten anhaken → Laden
4. **Erweiterungen → NAS2QGIS → Historie vergleichen...**
   - Layer wählen, zwei Lieferzeitpunkte wählen, Vergleichen

Bei jeder neuen Differenzabgabe: Import-Dialog erneut öffnen, nur die neue
Datei hinzufügen (bereits importierte werden anhand OID+Zeitstempel
automatisch als Duplikat erkannt und übersprungen), erneut importieren.

**Bei Code-Änderungen:** Nur die geänderte(n) Datei(en) in
`NAS2QGIS\NAS2QGIS\` ersetzen, dann `build_release.bat` ausführen und QGIS
komplett neu starten (nicht nur das Plugin neu installieren – sonst können
doppelte Menüeinträge entstehen, da QGIS beim Ersetzen laufender Plugins
nicht immer zuverlässig aufräumt).

---

## Bekannte Einschränkungen

- **Nur GID 7.x unterstützt.** GID 6-Lieferungen haben eine andere Struktur
  und schlagen beim Lesen der Kopfdaten fehl (nicht implementiert).
- **Delete-Sätze wurden noch nie an echten Daten getestet** – in allen
  bisherigen Testlieferungen kamen ausschließlich Insert/Replace vor. Die
  Logik dafür existiert und wurde durchdacht, aber nicht gegen reale
  Delete-Daten verifiziert.
- **`AX_BesondereFlurstuecksgrenze`** (Linienobjekt) hat aktuell keine
  Geometrie – anders als bei den Punktobjekten wurde die passende
  Relation zur Geometrie-Auflösung noch nicht identifiziert.
- **Zwei Erstabgaben unterschiedlicher Gebiete** lassen sich technisch
  problemlos in dasselbe GeoPackage einspielen, ein Vergleich über den
  Zeitpunkt der zweiten Erstabgabe hinweg zeigt deren gesamten Bestand
  dann aber als "neu" – das ist ein Lade-Artefakt, kein echtes
  Kataster-Ereignis.
- **Beteiligtenstatus setzt voraus, dass pro GeoPackage nur ein eigenes
  Verfahren vorkommt** (auch wenn mehrere BRB-Objekte benachbarter
  Verfahren mitgeliefert werden). Werden absichtlich mehrere eigene
  Verfahren in dasselbe GeoPackage eingelesen, liefert die
  "meiste Flurstücke"-Heuristik nur für eines davon ein sinnvolles
  Ergebnis.
- **PyQGIS-Code (Dialoge, `styling.py`, `beteiligung.py`) konnte nicht
  automatisiert getestet werden**, da in der Entwicklungsumgebung kein
  QGIS zur Verfügung stand – nur auf Syntaxfehler geprüft (`py_compile`).
  Mehrere reale Fehler (falsche API-Methodennamen, doppelte Menüeinträge,
  fehlende Geometriespalte nach Finalize, GEOS-Abstürze bei
  Geometrieprüfungen) wurden erst beim tatsächlichen Ausführen in QGIS
  gefunden und behoben.
- Keine automatisierten Tests (kein `pytest`-Setup); alle Prüfungen
  erfolgten ad hoc gegen echte Beispieldateien und einen LEFIS-Abgleich.

---

## Dateiübersicht

```
nas2qgis/
├── __init__.py             classFactory-Einstiegspunkt
├── metadata.txt              Plugin-Metadaten (Name, Version, Beschreibung)
├── nas2qgis_plugin.py         Hauptklasse: Menüeinträge, öffnet die Dialoge
├── import_dialog.py           Import-Dialog + Layer-Auswahl-Dialog
├── vergleich_dialog.py        Vergleichs-Dialog (Zustand A/B/Diff/Attribute)
├── styling.py                  Standard-Stilisierung neu geladener Layer
├── beteiligung.py               Beteiligtenstatus-Ermittlung (BRB-Zugehörigkeit)
├── parser.py                     NAS-XML-Parser (keine externen Abhängigkeiten)
└── writer.py                      Historisierter GeoPackage-Writer (nur sqlite3)

run_import.py                      CLI-Version des Imports (nutzt parser.py/writer.py)
build_release.bat                  Windows-Buildskript fürs Plugin-Release
anonymize_nas.py                    Nur für Testdaten: AX_Person deterministisch
                                      anonymisieren (nicht Teil des produktiven
                                      Imports/Plugins)
nas_historie_vergleich.py          QGIS-Processing-Algorithmus-Variante des
                                     Vergleichs (freie Datumswahl statt Auswahl
                                     aus Lieferungsliste, dafür in die
                                     Processing-Toolbox/Modelle einbindbar)
```
