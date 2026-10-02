"""
Schreibt NASFeature-Objekte historisiert in ein GeoPackage.

Kernidee (SCD2-Historisierung):
  - Insert  -> neue Zeile, gueltig_von = beginnt, gueltig_bis = NULL
  - Replace -> offene Zeile (gueltig_bis IS NULL) mit dieser OID wird geschlossen
               (gueltig_bis = beginnt der neuen Version), danach neue offene Zeile
  - Delete  -> offene Zeile wird geschlossen (gueltig_bis = Lieferungsdatum),
               keine neue Zeile
  - Update mit lebenszeitintervall/endet (Untergang mit Erhalt der Historie,
               in NBA-Lieferungen der Normalfall statt eines Delete)
            -> offene Zeile wird geschlossen (gueltig_bis = endet aus der
               Lieferung), keine neue Zeile

GeoPackage wird direkt ueber sqlite3 + manuell gebautem WKB/GPKG-Binaerformat
geschrieben, damit das Skript ohne GDAL/Fiona/PyQGIS lauffaehig ist.
"""

import os
import re
import sqlite3
import struct
from datetime import datetime, timezone

try:
    from .parser import parse_nas_file, parse_delivery_metadata, verfahrensnummer_aus_auftragsnummer
except ImportError:
    from parser import parse_nas_file, parse_delivery_metadata, verfahrensnummer_aus_auftragsnummer

WKB_TYPE = {"POINT": 1, "POLYGON": 3, "MULTIPOLYGON": 6}


# --------------------------------------------------------------------------
# WKT (wie von parser.geometry_to_wkt erzeugt) -> WKB -> GeoPackage-Blob
# --------------------------------------------------------------------------

def _split_top_level(s):
    parts, depth, current = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _parse_coord_structure(text):
    text = text.strip()
    if text.startswith("("):
        inner = text[1:-1]
        return [_parse_coord_structure(p) for p in _split_top_level(inner)]
    x, y = text.split()
    return (float(x), float(y))


def _wkb_ring(points):
    data = struct.pack("<I", len(points))
    for x, y in points:
        data += struct.pack("<dd", x, y)
    return data


def _wkb_polygon_body(rings):
    body = struct.pack("<I", len(rings))
    for ring in rings:
        body += _wkb_ring(ring)
    return struct.pack("<BI", 1, WKB_TYPE["POLYGON"]) + body


def wkt_to_wkb(wkt):
    wkt = wkt.strip()
    if wkt.startswith("POINT"):
        (x, y) = _parse_coord_structure(wkt[len("POINT"):].strip())[0] \
            if wkt[len("POINT"):].strip().startswith("(") and "," not in wkt else None
        # POINT (x y) -> Klammerinhalt direkt als "x y" parsen
        coords = wkt[wkt.index("(") + 1: wkt.rindex(")")]
        x, y = (float(v) for v in coords.split())
        return struct.pack("<BIdd", 1, WKB_TYPE["POINT"], x, y)

    if wkt.startswith("MULTIPOLYGON"):
        structure = _parse_coord_structure(wkt[len("MULTIPOLYGON"):].strip())
        body = struct.pack("<I", len(structure))
        for polygon_rings in structure:
            body += _wkb_polygon_body(polygon_rings)
        return struct.pack("<BI", 1, WKB_TYPE["MULTIPOLYGON"]) + body

    if wkt.startswith("POLYGON"):
        structure = _parse_coord_structure(wkt[len("POLYGON"):].strip())
        return _wkb_polygon_body(structure)

    raise ValueError(f"Unbekannter WKT-Typ: {wkt[:30]!r}")


def gpkg_geom_blob(wkb_bytes, srs_id):
    # GeoPackageBinaryHeader: Magic 'GP', Version, Flags, SRS-ID (kein Envelope)
    header = b"GP" + bytes([0]) + bytes([0b00000001]) + struct.pack("<i", srs_id)
    return header + wkb_bytes


def wkt_geometry_type(wkt):
    for t in ("MULTIPOLYGON", "POLYGON", "POINT"):
        if wkt.strip().startswith(t):
            return t
    raise ValueError(f"Unbekannter WKT-Typ: {wkt[:30]!r}")


# --------------------------------------------------------------------------
# GeoPackage-Grundgeruest
# --------------------------------------------------------------------------

GPKG_DDL = """
CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
    srs_name TEXT NOT NULL,
    srs_id INTEGER NOT NULL PRIMARY KEY,
    organization TEXT NOT NULL,
    organization_coordsys_id INTEGER NOT NULL,
    definition TEXT NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS gpkg_contents (
    table_name TEXT NOT NULL PRIMARY KEY,
    data_type TEXT NOT NULL,
    identifier TEXT UNIQUE,
    description TEXT DEFAULT '',
    last_change TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    min_x REAL, min_y REAL, max_x REAL, max_y REAL,
    srs_id INTEGER,
    CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
);
CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
    table_name TEXT NOT NULL,
    column_name TEXT NOT NULL,
    geometry_type_name TEXT NOT NULL,
    srs_id INTEGER NOT NULL,
    z TINYINT NOT NULL,
    m TINYINT NOT NULL,
    CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name)
);
CREATE TABLE IF NOT EXISTS nas_lieferungen (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dateiname TEXT NOT NULL,
    antragsnummer TEXT,
    auftragsnummer TEXT,
    abgabeintervallBeginn TEXT,
    abgabeintervallEnde TEXT NOT NULL,
    importiert_am TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def _wkb_points_iter(wkb, pos, gtype):
    """Liefert alle (x,y)-Punkte einer WKB-Geometrie (Point/Polygon/MultiPolygon)."""
    if gtype == 1:
        x, y = struct.unpack_from("<dd", wkb, pos)
        yield x, y
    elif gtype == 3:
        numrings = struct.unpack_from("<I", wkb, pos)[0]
        pos += 4
        for _ in range(numrings):
            numpts = struct.unpack_from("<I", wkb, pos)[0]
            pos += 4
            for _ in range(numpts):
                x, y = struct.unpack_from("<dd", wkb, pos)
                yield x, y
                pos += 16
    elif gtype == 6:
        numpoly = struct.unpack_from("<I", wkb, pos)[0]
        pos += 4
        for _ in range(numpoly):
            pos += 5
            numrings = struct.unpack_from("<I", wkb, pos)[0]
            pos += 4
            for _ in range(numrings):
                numpts = struct.unpack_from("<I", wkb, pos)[0]
                pos += 4
                for _ in range(numpts):
                    x, y = struct.unpack_from("<dd", wkb, pos)
                    yield x, y
                    pos += 16


def _wkb_blob_bbox(blob):
    flags = blob[3]
    envelope_code = (flags >> 1) & 0b111
    envelope_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[envelope_code]
    offset = 8 + envelope_bytes
    wkb = blob[offset:]
    gtype = struct.unpack_from("<I", wkb, 1)[0]
    xs, ys = [], []
    for x, y in _wkb_points_iter(wkb, 5, gtype):
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


class GpkgWriter:
    def __init__(self, path, srs_id=25833, srs_definition="EPSG:25833 ETRS89 / UTM zone 33N"):
        self.path = path
        self.srs_id = srs_id
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA application_id = 0x47504B47")  # 'GPKG'
        self.conn.execute("PRAGMA user_version = 10300")
        self.conn.executescript(GPKG_DDL)
        try:
            self.conn.execute("ALTER TABLE nas_lieferungen ADD COLUMN auftragsnummer TEXT")
        except sqlite3.OperationalError:
            pass  # Spalte existiert schon (neu angelegte Tabelle oder bereits migriert)
        self._ensure_srs(srs_id, srs_definition)
        self.table_columns = {}  # table_name -> set(column_names) Cache

    def _ensure_srs(self, srs_id, definition):
        rows = {
            -1: ("Undefined cartesian SRS", -1, "NONE", -1, "undefined"),
            0: ("Undefined geographic SRS", 0, "NONE", 0, "undefined"),
            4326: ("WGS 84", 4326, "EPSG", 4326, "WGS84"),
            srs_id: (definition, srs_id, "EPSG", srs_id, definition),
        }
        for sid, (name, _, org, org_id, defn) in rows.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO gpkg_spatial_ref_sys "
                "(srs_name, srs_id, organization, organization_coordsys_id, definition) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, sid, org, org_id, defn),
            )

    def _ensure_table(self, table, geometry_type):
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        exists = cur.fetchone() is not None

        if not exists:
            self.conn.execute(f"""
                CREATE TABLE "{table}" (
                    fid INTEGER PRIMARY KEY AUTOINCREMENT,
                    oid TEXT NOT NULL,
                    gueltig_von TEXT,
                    gueltig_bis TEXT,
                    geom BLOB
                )
            """)
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_oid" ON "{table}"(oid)')
            self.conn.execute(
                "INSERT OR IGNORE INTO gpkg_contents (table_name, data_type, identifier, srs_id) "
                "VALUES (?, 'features', ?, ?)",
                (table, table, self.srs_id),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO gpkg_geometry_columns "
                "(table_name, column_name, geometry_type_name, srs_id, z, m) "
                "VALUES (?, 'geom', ?, ?, 0, 0)",
                (table, geometry_type or "GEOMETRY", self.srs_id),
            )
            self.table_columns[table] = {"fid", "oid", "gueltig_von", "gueltig_bis", "geom"}
            return

        if table not in self.table_columns:
            cur = self.conn.execute(f'PRAGMA table_info("{table}")')
            self.table_columns[table] = {row[1] for row in cur.fetchall()}

        if geometry_type and "geom" not in self.table_columns[table]:
            # Tabelle wurde bei einem frueheren finalize_geopackage()-Lauf als
            # reine Attributtabelle eingestuft (hatte bis dahin nie Geometrie),
            # bekommt jetzt aber doch eine Geometrie geliefert - geom-Spalte
            # wieder ergaenzen und Tabelle als Feature-Tabelle zurueckstufen.
            self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN geom BLOB')
            self.table_columns[table].add("geom")
            self.conn.execute(
                "INSERT OR IGNORE INTO gpkg_geometry_columns "
                "(table_name, column_name, geometry_type_name, srs_id, z, m) "
                "VALUES (?, 'geom', ?, ?, 0, 0)",
                (table, geometry_type, self.srs_id),
            )
            self.conn.execute(
                "UPDATE gpkg_contents SET data_type = 'features' WHERE table_name = ?", (table,)
            )

    def _ensure_columns(self, table, attr_keys):
        missing = attr_keys - self.table_columns[table]
        for col in missing:
            safe_col = re.sub(r"[^A-Za-z0-9_]", "_", col)
            self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{safe_col}" TEXT')
            self.table_columns[table].add(col)

    def _geom_blob(self, wkt):
        if not wkt:
            return None
        wkb = wkt_to_wkb(wkt)
        return gpkg_geom_blob(wkb, self.srs_id)

    def _close_open_row(self, table, oid, gueltig_bis):
        self.conn.execute(
            f'UPDATE "{table}" SET gueltig_bis = ? WHERE oid = ? AND gueltig_bis IS NULL',
            (gueltig_bis, oid),
        )

    def _insert_row(self, feature):
        table = feature.objektart
        geom_type = wkt_geometry_type(feature.geometry_wkt) if feature.geometry_wkt else None
        self._ensure_table(table, geom_type)
        self._ensure_columns(table, set(feature.attributes.keys()))

        hat_geom_spalte = "geom" in self.table_columns[table]
        cols = ["oid", "gueltig_von", "gueltig_bis"] + (["geom"] if hat_geom_spalte else []) + list(feature.attributes.keys())
        safe_cols = ["oid", "gueltig_von", "gueltig_bis"] + (["geom"] if hat_geom_spalte else []) + \
                    [re.sub(r"[^A-Za-z0-9_]", "_", k) for k in feature.attributes.keys()]
        placeholders = ", ".join("?" for _ in safe_cols)
        col_list = ", ".join(f'"{c}"' for c in safe_cols)
        values = [feature.oid, feature.beginnt, None] + \
                 ([self._geom_blob(feature.geometry_wkt)] if hat_geom_spalte else []) + \
                 list(feature.attributes.values())
        self.conn.execute(f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})', values)

    def apply_insert(self, feature):
        table = feature.objektart
        geom_type = wkt_geometry_type(feature.geometry_wkt) if feature.geometry_wkt else None
        self._ensure_table(table, geom_type)

        # Fall A: exakt dieselbe Version (gleiche OID, gleiches beginnt) schon
        # vorhanden - z.B. ein erneut mitgeliefertes Kontextobjekt. Einfach
        # ueberspringen, kein Duplikat anlegen.
        cur = self.conn.execute(
            f'SELECT 1 FROM "{table}" WHERE oid = ? AND gueltig_von = ?',
            (feature.oid, feature.beginnt),
        )
        if cur.fetchone():
            return

        # Fall B: Es gibt bereits eine OFFENE Zeile fuer diese OID, aber mit
        # einem ANDEREN beginnt - das Objekt hat sich also zwischenzeitlich
        # veraendert, wurde uns aber nicht als Replace, sondern erneut als
        # Insert geliefert (z.B. bei einem vollen Re-Export/zweiter Erstabgabe
        # desselben Gebiets statt einer Differenzabgabe). Ohne diese Pruefung
        # wuerden zwei parallel "offene" Zeilen fuer dieselbe OID entstehen.
        # Deshalb hier wie bei einem Replace behandeln: alte Zeile schliessen,
        # neue oeffnen.
        cur = self.conn.execute(
            f'SELECT 1 FROM "{table}" WHERE oid = ? AND gueltig_bis IS NULL',
            (feature.oid,),
        )
        if cur.fetchone():
            self._close_open_row(table, feature.oid, feature.beginnt)

        self._insert_row(feature)

    def apply_replace(self, feature):
        table = feature.objektart
        geom_type = wkt_geometry_type(feature.geometry_wkt) if feature.geometry_wkt else None
        self._ensure_table(table, geom_type)
        self._close_open_row(table, feature.oid, feature.beginnt)
        self._insert_row(feature)

    def _objekttabellen(self, bevorzugt=None):
        """Alle historisierten Objektart-Tabellen IM GEOPACKAGE (nicht nur
        die in diesem Importlauf schon angefassten - table_columns startet
        je Lieferung leer). Die Tabelle der gelieferten Objektart zuerst."""
        tabellen = [
            r[0] for r in self.conn.execute(
                "SELECT table_name FROM gpkg_contents WHERE table_name != 'nas_lieferungen'"
            )
        ]
        ergebnis = []
        for table in tabellen:
            if table not in self.table_columns:
                cur = self.conn.execute(f'PRAGMA table_info("{table}")')
                self.table_columns[table] = {row[1] for row in cur.fetchall()}
            if {"oid", "gueltig_bis"} <= self.table_columns[table]:
                ergebnis.append(table)
        if bevorzugt in ergebnis:
            ergebnis.remove(bevorzugt)
            ergebnis.insert(0, bevorzugt)
        return ergebnis

    def _offene_zeile(self, oid, objektart=None):
        """(tabelle, fid, gueltig_von) der offenen Zeile dieser OID oder None."""
        for table in self._objekttabellen(objektart):
            row = self.conn.execute(
                f'SELECT fid, gueltig_von FROM "{table}" WHERE oid = ? AND gueltig_bis IS NULL',
                (oid,),
            ).fetchone()
            if row:
                return table, row[0], row[1]
        return None

    def apply_delete(self, feature, delivery_ende):
        """True = offene Zeile geschlossen, False = OID nicht gefunden."""
        treffer = self._offene_zeile(feature.oid, feature.objektart)
        if treffer is None:
            return False
        table, fid, _ = treffer
        self.conn.execute(f'UPDATE "{table}" SET gueltig_bis = ? WHERE fid = ?', (delivery_ende, fid))
        return True

    def apply_update(self, feature):
        """wfs:Update. Angewendet wird nur das Setzen von endet (Untergang).
        Rueckgabe:
          "untergang"           - offene Zeile mit gueltig_bis = endet geschlossen
          "bereits_geschlossen" - die betroffene Version war schon geschlossen,
                                  z.B. weil ein Replace derselben OID in dieser
                                  Lieferung vorher verarbeitet wurde
          "nicht_gefunden"      - OID in keiner Tabelle vorhanden
          "ignoriert"           - Update ohne endet (andere Eigenschaften)
        """
        endet = feature.endet
        if not endet:
            return "ignoriert"
        treffer = self._offene_zeile(feature.oid, feature.objektart)
        if treffer is not None:
            table, fid, gueltig_von = treffer
            # Schutz: nur eine Version schliessen, die VOR dem Untergang
            # begonnen hat. Beginnt die offene Version bei/nach endet, ist es
            # bereits die NEUE Version eines Replace derselben Lieferung -
            # die alte wurde dabei schon geschlossen.
            if gueltig_von is None or gueltig_von < endet:
                self.conn.execute(f'UPDATE "{table}" SET gueltig_bis = ? WHERE fid = ?', (endet, fid))
                return "untergang"
            return "bereits_geschlossen"
        for table in self._objekttabellen(feature.objektart):
            if self.conn.execute(f'SELECT 1 FROM "{table}" WHERE oid = ? LIMIT 1', (feature.oid,)).fetchone():
                return "bereits_geschlossen"
        return "nicht_gefunden"

    def _compute_and_store_extents(self):
        """Berechnet min/max x/y je Geometrietabelle aus den WKB-Blobs und
        schreibt sie in gpkg_contents - fehlende Extents sind eine haeufige
        Ursache fuer Abstuerze/Fehler beim Oeffnen in QGIS/GDAL."""
        tables = [r[0] for r in self.conn.execute("SELECT table_name FROM gpkg_geometry_columns")]
        for table in tables:
            bbox = None
            for (blob,) in self.conn.execute(f'SELECT geom FROM "{table}" WHERE geom IS NOT NULL'):
                result = _wkb_blob_bbox(blob)
                if result is None:
                    continue
                x1, y1, x2, y2 = result
                if bbox is None:
                    bbox = [x1, y1, x2, y2]
                else:
                    bbox[0] = min(bbox[0], x1)
                    bbox[1] = min(bbox[1], y1)
                    bbox[2] = max(bbox[2], x2)
                    bbox[3] = max(bbox[3], y2)
            if bbox:
                self.conn.execute(
                    'UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?',
                    (*bbox, table),
                )

    def _finalize_geometryless_tables(self):
        """
        Tabellen, bei denen ueber die gesamte Verarbeitung KEINE einzige Zeile
        eine Geometrie hatte, sollten laut GeoPackage-Spezifikation gar keine
        Geometriespalte registrieren, sondern als reine Attributtabelle
        (data_type='attributes') gefuehrt werden. Der vorherige Fallback-Ansatz
        (generischer 'GEOMETRY'-Typ mit durchgehend NULL-Werten) hat GDAL beim
        Aufbau der Layer-Liste offenbar durcheinandergebracht - deshalb hier
        sauber nachbereinigen, statt es beim Schreiben zu raten.
        """
        geom_tables = [r[0] for r in self.conn.execute("SELECT table_name FROM gpkg_geometry_columns")]
        for table in geom_tables:
            count = self.conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE geom IS NOT NULL').fetchone()[0]
            if count > 0:
                continue
            try:
                self.conn.execute(f'ALTER TABLE "{table}" DROP COLUMN geom')
            except sqlite3.OperationalError:
                pass  # aeltere SQLite-Version - Spalte bleibt physisch, Registrierung wird trotzdem bereinigt
            self.conn.execute('DELETE FROM gpkg_geometry_columns WHERE table_name = ?', (table,))
            self.conn.execute(
                "UPDATE gpkg_contents SET data_type = 'attributes', min_x=NULL, min_y=NULL, max_x=NULL, max_y=NULL "
                "WHERE table_name = ?", (table,)
            )

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()


def finalize_geopackage(gpkg_path):
    """
    Einmalig NACH dem Einspielen ALLER Lieferungen (Erstabgabe + saemtliche
    Differenzabgaben) aufrufen - raeumt Tabellen auf, die durchgehend keine
    Geometrie hatten, und berechnet die Bounding-Box-Extents. Nicht nach jeder
    einzelnen import_delivery()-Datei aufrufen, sonst fehlt bei einer spaeteren
    Differenzabgabe die geom-Spalte fuer Objektarten, die vorher zufaellig
    immer geometrielos waren, aber es spaeter doch nicht sind.
    """
    # SRS aus der bereits geschriebenen Datei uebernehmen statt erneut zu raten
    probe = sqlite3.connect(gpkg_path)
    row = probe.execute("SELECT srs_id FROM gpkg_geometry_columns LIMIT 1").fetchone()
    probe.close()
    srs_id = row[0] if row else 25833

    writer = GpkgWriter(gpkg_path, srs_id=srs_id)
    writer._finalize_geometryless_tables()
    writer._compute_and_store_extents()
    writer.close()


CRS_URN_TO_EPSG = {
    "urn:adv:crs:ETRS89_UTM32": 25832,
    "urn:adv:crs:ETRS89_UTM33": 25833,
    "urn:adv:crs:DE_DHDN_3GK2": 31466,
    "urn:adv:crs:DE_DHDN_3GK3": 31467,
    "urn:adv:crs:DE_DHDN_3GK4": 31468,
    "urn:adv:crs:DE_DHDN_3GK5": 31469,
}


def pruefe_bereits_importiert(gpkg_path, meta, dateiname):
    """Prueft, ob eine Lieferung mit DEMSELBEN Dateinamen oder demselben
    abgabeintervallEnde bereits in dieses GeoPackage eingespielt wurde -
    unabhaengig davon, ob es die zuletzt eingespielte Lieferung ist oder
    eine aeltere. Das faengt genau den Fall ab, dass eine Lieferung ein
    zweites Mal eingespielt wird (z.B. weil sie noch von einem frueheren
    Importlauf in der Dateiliste stehen geblieben war) - erneutes Einspielen
    wuerde ihre Insert/Replace/Delete-Saetze ein zweites Mal auf einen
    Bestand anwenden, der sie schon enthaelt, und die Historisierung
    verfaelschen. Gibt einen Warntext zurueck, falls ja - sonst None."""
    if not os.path.exists(gpkg_path):
        return None
    conn = sqlite3.connect(gpkg_path)
    try:
        rows = conn.execute(
            "SELECT dateiname, abgabeintervallEnde, importiert_am FROM nas_lieferungen "
            "WHERE dateiname = ? OR abgabeintervallEnde = ?",
            (dateiname, meta.get("abgabeintervallEnde")),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    if not rows:
        return None
    alter_dateiname, alte_abgabe_ende, importiert_am = rows[0]
    return (
        f"Eine Lieferung mit demselben Dateinamen oder demselben Lieferdatum wurde "
        f"bereits am {importiert_am} eingespielt "
        f"('{alter_dateiname}', abgabeintervallEnde={alte_abgabe_ende}).\n"
        f"Erneutes Einspielen wendet dieselben Änderungen ein zweites Mal an und "
        f"verfälscht die Historisierung."
    )


def pruefe_chronologische_reihenfolge(gpkg_path, meta):
    """Prueft, ob die neue Lieferung AELTER ist als die zuletzt in dieses
    GeoPackage eingespielte (aus nas_lieferungen). Gibt einen Warntext
    zurueck, falls ja - sonst None. Faengt genau den Fall ab, dass zwei
    Lieferungen in getrennten Importlaeufen (nicht gemeinsam ausgewaehlt und
    damit nicht automatisch sortiert) in falscher Reihenfolge eingespielt
    werden - das wuerde die Historisierung verfaelschen (die offene Zeile
    wird immer chronologisch "vorwaerts" geschlossen, nicht rueckwirkend
    eingefuegt)."""
    neu = meta.get("abgabeintervallEnde")
    if neu is None or not os.path.exists(gpkg_path):
        return None
    conn = sqlite3.connect(gpkg_path)
    try:
        row = conn.execute(
            "SELECT dateiname, abgabeintervallEnde FROM nas_lieferungen "
            "ORDER BY abgabeintervallEnde DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is None:
        return None
    letzter_dateiname, letztes_datum = row
    if neu < letztes_datum:
        return (
            f"Diese Lieferung (abgabeintervallEnde={neu}) ist ÄLTER als die zuletzt "
            f"eingespielte '{letzter_dateiname}' (abgabeintervallEnde={letztes_datum}).\n"
            f"Werden Lieferungen nicht in chronologischer Reihenfolge eingespielt, "
            f"wird die Historisierung verfälscht."
        )
    return None


def pruefe_verfahren(gpkg_path, meta):
    """Prueft, ob die neue Lieferung zu einem ANDEREN Verfahren gehoert als
    die bereits im GeoPackage vorhandenen (Verfahrensnummer aus
    auftragsnummer, Rueckfall antragsnummer - siehe
    verfahrensnummer_aus_auftragsnummer()). Gibt einen Warntext zurueck,
    falls ja - sonst None.

    Bewusst nur eine Warnung, kein harter Block: Nicht jedes Katasteramt
    liefert auftragsnummer im erwarteten Muster, die Erkennung ist also ein
    Best-Effort-Hinweis fuers Vier-Augen-Prinzip, keine Garantie. Kann eine
    Verfahrensnummer weder fuer die neue noch fuer die vorhandenen
    Lieferungen ermittelt werden, wird stillschweigend nicht gewarnt (lieber
    keine Warnung als eine falsche)."""
    neu = (
        verfahrensnummer_aus_auftragsnummer(meta.get("auftragsnummer"))
        or verfahrensnummer_aus_auftragsnummer(meta.get("antragsnummer"))
    )
    if neu is None or not os.path.exists(gpkg_path):
        return None

    conn = sqlite3.connect(gpkg_path)
    try:
        rows = conn.execute("SELECT antragsnummer, auftragsnummer FROM nas_lieferungen").fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    if not rows:
        return None

    vorhandene = set()
    for antragsnummer, auftragsnummer in rows:
        v = verfahrensnummer_aus_auftragsnummer(auftragsnummer) or verfahrensnummer_aus_auftragsnummer(antragsnummer)
        if v:
            vorhandene.add(v)
    if not vorhandene or neu in vorhandene:
        return None

    return (
        f"Diese Lieferung gehört zu Verfahren '{neu}'. Im GeoPackage sind bisher nur "
        f"Lieferungen aus Verfahren {', '.join(sorted(vorhandene))} enthalten.\n"
        f"Das könnte eine versehentlich falsche Datei sein."
    )


def import_delivery(gpkg_path, nas_path, srs_id=None):
    """Spielt eine einzelne NAS-Datei (Erst- oder Differenzabgabe) historisiert
    in ein (ggf. neues) GeoPackage ein. Dateien MUESSEN chronologisch
    nacheinander mit dieser Funktion verarbeitet werden.

    srs_id=None (Default): CRS automatisch aus dem <crs>-Element im Dateikopf
    ableiten (z.B. ETRS89_UTM32 -> EPSG:25832). Nur explizit angeben, wenn die
    automatische Erkennung fehlschlaegt oder ihr ein anderes CRS erzwingen wollt.
    """
    meta = parse_delivery_metadata(nas_path)

    if srs_id is None:
        crs_urn = meta.get("crs")
        srs_id = CRS_URN_TO_EPSG.get(crs_urn)
        if srs_id is None:
            raise ValueError(
                f"CRS '{crs_urn}' aus der Datei konnte nicht automatisch einem "
                f"EPSG-Code zugeordnet werden. Bitte srs_id explizit angeben, "
                f"z.B. import_delivery(gpkg, datei, srs_id=25833). "
                f"Bekannte CRS: {list(CRS_URN_TO_EPSG.keys())}"
            )

    writer = GpkgWriter(gpkg_path, srs_id=srs_id)

    counts = {
        "insert": 0, "replace": 0, "delete": 0,
        "untergang": 0,              # Update mit endet -> Objekt beendet
        "bereits_geschlossen": 0,    # Update endet, Version war schon geschlossen
        "update_ignoriert": 0,       # Update auf andere Eigenschaften (nicht angewendet)
        "nicht_gefunden": 0,         # Delete/Update auf unbekannte OID
    }
    for feature in parse_nas_file(nas_path):
        if feature.action == "insert":
            writer.apply_insert(feature)
            counts["insert"] += 1
        elif feature.action == "replace":
            writer.apply_replace(feature)
            counts["replace"] += 1
        elif feature.action == "delete":
            counts["delete"] += 1
            if not writer.apply_delete(feature, meta.get("abgabeintervallEnde")):
                counts["nicht_gefunden"] += 1
        elif feature.action == "update":
            ergebnis = writer.apply_update(feature)
            counts["update_ignoriert" if ergebnis == "ignoriert" else ergebnis] += 1

    writer.conn.execute(
        "INSERT INTO nas_lieferungen (dateiname, antragsnummer, auftragsnummer, abgabeintervallBeginn, abgabeintervallEnde) "
        "VALUES (?, ?, ?, ?, ?)",
        (os.path.basename(nas_path), meta.get("antragsnummer"), meta.get("auftragsnummer"),
         meta.get("abgabeintervallBeginn"), meta.get("abgabeintervallEnde")),
    )
    writer.conn.execute(
        "INSERT OR IGNORE INTO gpkg_contents (table_name, data_type, identifier) "
        "VALUES ('nas_lieferungen', 'attributes', 'nas_lieferungen')"
    )

    writer.close()
    return counts, meta
