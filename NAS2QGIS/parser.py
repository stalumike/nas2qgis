"""
Prototyp-Parser fuer NAS/ALKIS-Aenderungsdateien (GID 7.1 / GeoInfoDok).

Liest eine NAS-XML-Datei (Erst- oder Differenzabgabe, verpackt als
AX_NutzerbezogeneBestandsdatenaktualisierung_NBA mit wfs:Transaction)
und liefert je Objekt einen NASFeature-Datensatz mit:
  - action:      "insert" | "replace" | "delete"
  - objektart:   z.B. "AX_Flurstueck"
  - oid:         stabile ADV-OID (ohne "urn:adv:oid:"-Praefix)
  - gml_id:      technische gml:id (kann bei Replace einen Zeitstempel-Suffix haben!)
  - attributes:  dict der einfachen Attribut-Kindelemente
  - geometry_wkt: WKT-String oder None (z.B. bei Delete oder rein relationalen Objekten)

Bewusst OHNE lxml-Abhaengigkeit zu shapely/GDAL fuer die Geometrie-Logik,
damit sich das Skript einfach isoliert testen laesst.
"""

from dataclasses import dataclass, field
import xml.etree.ElementTree as etree

GML_NS = "http://www.opengis.net/gml/3.2"
WFS_NS = "http://www.opengis.net/wfs/2.0"
FES_NS = "http://www.opengis.net/fes/2.0"
XLINK_NS = "http://www.w3.org/1999/xlink"

GEOMETRY_ROOT_TAGS = {"MultiSurface", "Surface", "Polygon", "Point", "MultiPoint", "Curve", "MultiCurve"}


def local(tag):
    """Namespace-Prefix von einem lxml-Tag abtrennen, z.B. '{ns}Foo' -> 'Foo'."""
    return tag.split("}")[-1] if "}" in tag else tag


def q(ns, tag):
    return f"{{{ns}}}{tag}"


@dataclass
class NASFeature:
    action: str
    objektart: str
    oid: str | None
    gml_id: str | None
    beginnt: str | None = None  # lebenszeitintervall/beginnt - Start dieser Objektversion
    endet: str | None = None    # lebenszeitintervall/endet - nur bei historisierten/geschlossenen Saetzen befuellt
    attributes: dict = field(default_factory=dict)
    geometry_wkt: str | None = None


def parse_poslist(text):
    """'x1 y1 x2 y2 ...' -> [(x1,y1), (x2,y2), ...]"""
    vals = [float(v) for v in text.split()]
    return list(zip(vals[0::2], vals[1::2]))


import math


def _kreis_durch_drei_punkte(p1, p2, p3):
    """Mittelpunkt und Radius des Kreises durch drei Punkte. None, falls die
    Punkte (nahezu) auf einer Geraden liegen (kein eindeutiger Kreis)."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy), r


def _arc_interpolieren(p1, p2, p3, max_segmentlaenge=0.5):
    """Interpoliert einen Kreisbogen (Start-, Zwischen- und Endpunkt, wie in
    gml:Arc/gml:ArcString) als Punktfolge, damit er als Polygon-Ring
    dargestellt werden kann - GeoPackage/WKB kennt keine echten Boegen."""
    kreis = _kreis_durch_drei_punkte(p1, p2, p3)
    if kreis is None:
        return [p1, p3]  # entartet (Punkte auf einer Linie) -> gerade Strecke

    (cx, cy), r = kreis
    w1 = math.atan2(p1[1] - cy, p1[0] - cx)
    w2 = math.atan2(p2[1] - cy, p2[0] - cx)
    w3 = math.atan2(p3[1] - cy, p3[0] - cx)

    # Winkel relativ zu w1 in [0, 2*pi) normalisieren, um die Schwenkrichtung
    # (ueber w2) und den Gesamtwinkel bis w3 eindeutig zu bestimmen
    def rel(w):
        d = (w - w1) % (2 * math.pi)
        return d

    w2_rel = rel(w2)
    w3_rel = rel(w3)
    if w2_rel > w3_rel:
        # Zwischenpunkt liegt hinter dem Endpunkt -> Bogen verlaeuft im
        # Uhrzeigersinn (negative Richtung) statt gegen den Uhrzeigersinn
        w3_rel -= 2 * math.pi

    bogenlaenge = abs(w3_rel) * r
    anzahl_segmente = max(4, int(bogenlaenge / max_segmentlaenge) + 1)

    return [
        (cx + r * math.cos(w1 + w3_rel * i / anzahl_segmente),
         cy + r * math.sin(w1 + w3_rel * i / anzahl_segmente))
        for i in range(anzahl_segmente + 1)
    ]


def _segment_punkte(segment_elem):
    """Punktfolge eines einzelnen Curve-Segments - gerade (LineStringSegment)
    oder Kreisbogen (Arc/ArcString, ggf. mehrere aneinandergereihte Boegen als
    Dreiergruppen: Start/Zwischenpunkt/Ende, Ende=Start des naechsten Bogens)."""
    tag = local(segment_elem.tag)
    poslist_elem = segment_elem.find(q(GML_NS, "posList"))
    if poslist_elem is None or not poslist_elem.text:
        return []
    rohpunkte = parse_poslist(poslist_elem.text)

    if tag not in ("Arc", "ArcString"):
        return rohpunkte

    ergebnis = []
    i = 0
    while i + 2 < len(rohpunkte):
        bogen = _arc_interpolieren(rohpunkte[i], rohpunkte[i + 1], rohpunkte[i + 2])
        if ergebnis and ergebnis[-1] == bogen[0]:
            ergebnis.extend(bogen[1:])
        else:
            ergebnis.extend(bogen)
        i += 2
    return ergebnis


def build_ring_points(ring_elem, curve_lookup):
    """
    Baut die Punktfolge eines gml:Ring aus seinen curveMember-Segmenten zusammen.
    ALKIS speichert Grenzlinien oft als mehrere kurze LineStringSegments statt
    einer durchgehenden posList - deshalb muessen wir sie hier verketten und
    doppelte Verbindungspunkte zwischen den Segmenten herausfiltern.
    """
    points = []
    for curve_member in ring_elem.findall(q(GML_NS, "curveMember")):
        curve = curve_member.find(q(GML_NS, "Curve"))
        if curve is None:
            # Kurve wird per xlink:href referenziert (gemeinsame Grenze,
            # an anderer Stelle in der Datei bereits definiert).
            href = curve_member.get(q(XLINK_NS, "href"))
            if href and href.lstrip("#") in curve_lookup:
                seg_points = curve_lookup[href.lstrip("#")]
            else:
                # Kurve nicht in dieser Datei aufloesbar (z.B. Verweis auf
                # ein Objekt aus einer fremden Gemarkung) - fuer den Prototyp
                # ueberspringen wir das Segment und melden es separat.
                continue
        else:
            seg_points = []
            segments = curve.find(q(GML_NS, "segments"))
            for segment_elem in segments:
                punkte = _segment_punkte(segment_elem)
                if seg_points and punkte and seg_points[-1] == punkte[0]:
                    seg_points.extend(punkte[1:])
                else:
                    seg_points.extend(punkte)

        if points and seg_points and points[-1] == seg_points[0]:
            points.extend(seg_points[1:])
        else:
            points.extend(seg_points)

    # Ring schliessen, falls nicht bereits geschlossen
    if points and points[0] != points[-1]:
        points.append(points[0])

    return points


def index_curves(root):
    """
    Einmalig alle gml:Curve-Elemente mit gml:id in der Datei indizieren,
    damit xlink:href-Referenzen auf gemeinsame Grenzsegmente aufgeloest
    werden koennen (kommt in dieser Beispieldatei nicht vor, aber im
    allgemeinen AAA-Modell durchaus).
    """
    lookup = {}
    for curve in root.iter(q(GML_NS, "Curve")):
        gid = curve.get(q(GML_NS, "id"))
        if not gid:
            continue
        segments = curve.find(q(GML_NS, "segments"))
        if segments is None:
            continue
        pts = []
        for segment_elem in segments:
            punkte = _segment_punkte(segment_elem)
            if pts and punkte and pts[-1] == punkte[0]:
                pts.extend(punkte[1:])
            else:
                pts.extend(punkte)
        lookup[gid] = pts
    return lookup


def build_polygon_wkt(surface_elem, curve_lookup):
    """gml:Surface -> WKT-Ringe [(exterior), (interior1), ...] als Liste von Punktlisten."""
    rings = []
    patches = surface_elem.find(q(GML_NS, "patches"))
    for patch in patches.findall(q(GML_NS, "PolygonPatch")):
        exterior = patch.find(q(GML_NS, "exterior"))
        ext_ring = exterior.find(q(GML_NS, "Ring"))
        rings.append(build_ring_points(ext_ring, curve_lookup))
        for interior in patch.findall(q(GML_NS, "interior")):
            int_ring = interior.find(q(GML_NS, "Ring"))
            rings.append(build_ring_points(int_ring, curve_lookup))
    return rings


def ring_to_wkt(points):
    return "(" + ", ".join(f"{x:.3f} {y:.3f}" for x, y in points) + ")"


def find_geometry_root(object_elem):
    """
    Sucht das erste GML-Geometrie-Root-Element irgendwo unterhalb des Objekts
    (statt den Container-Tagnamen wie 'position' hart zu kodieren - der kann
    je nach Objektart/Profil variieren).
    """
    for elem in object_elem.iter():
        if local(elem.tag) in GEOMETRY_ROOT_TAGS:
            return elem
    return None


def geometry_to_wkt(geom_elem, curve_lookup):
    tag = local(geom_elem.tag)
    if tag == "Point":
        pos = geom_elem.find(q(GML_NS, "pos")).text
        x, y = pos.split()
        return f"POINT ({float(x):.3f} {float(y):.3f})"

    if tag in ("Surface", "Polygon"):
        rings = build_polygon_wkt(geom_elem, curve_lookup)
        return "POLYGON (" + ", ".join(ring_to_wkt(r) for r in rings) + ")"

    if tag == "MultiSurface":
        polygons = []
        for member in geom_elem.findall(q(GML_NS, "surfaceMember")):
            surface = member.find(q(GML_NS, "Surface"))
            rings = build_polygon_wkt(surface, curve_lookup)
            polygons.append("(" + ", ".join(ring_to_wkt(r) for r in rings) + ")")
        if len(polygons) == 1:
            return "POLYGON " + polygons[0]
        return "MULTIPOLYGON (" + ", ".join(polygons) + ")"

    # weitere Typen (MultiPoint, MultiCurve, ...) bei Bedarf ergaenzen
    return None


def extract_lebenszeitintervall(object_elem):
    """Liefert (beginnt, endet) als ISO-Zeitstrings oder (None, None)."""
    lzi = None
    for child in object_elem:
        if local(child.tag) == "lebenszeitintervall":
            lzi = child
            break
    if lzi is None:
        return None, None
    aa = None
    for child in lzi:
        aa = child  # i.d.R. genau ein AA_Lebenszeitintervall-Kind
        break
    if aa is None:
        return None, None
    beginnt = None
    endet = None
    for child in aa:
        tag = local(child.tag)
        if tag == "beginnt":
            beginnt = child.text
        elif tag == "endet":
            endet = child.text
    return beginnt, endet


def parse_delivery_metadata(path):
    """Liest die Kopfdaten einer NAS-Lieferung (fuer Delete-Faelle ohne eigenes
    lebenszeitintervall und zur chronologischen Sortierung mehrerer Dateien)."""
    tree = etree.parse(path)
    root = tree.getroot()
    meta = {}
    for tag in ("antragsnummer", "auftragsnummer", "abgabeintervallBeginn", "abgabeintervallEnde", "profilkennung"):
        elem = None
        for e in root.iter():
            if local(e.tag) == tag:
                elem = e
                break
        meta[tag] = elem.text if elem is not None else None

    crs_elem = None
    for e in root.iter():
        if local(e.tag) == "crs":
            crs_elem = e
            break
    meta["crs"] = crs_elem.get(q(XLINK_NS, "href")) if crs_elem is not None else None
    return meta


def verfahrensnummer_aus_auftragsnummer(auftragsnummer):
    """Extrahiert die Verfahrensnummer aus der auftragsnummer, z.B.
    '130071-32223_BOV_Wokuhl_8' -> '32223' (Amtskennung-Verfahrensnummer_BOV_
    Name_Abgabenummer). None, falls das erwartete Muster nicht passt."""
    if not auftragsnummer or "-" not in auftragsnummer:
        return None
    rest = auftragsnummer.split("-", 1)[1]
    verfahrensnummer = rest.split("_", 1)[0]
    return verfahrensnummer or None


def extract_attributes(object_elem):
    """
    Sammelt einfache (skalare Text-)Kindelemente als Attribute.
    Komplexe verschachtelte Strukturen (Fachdatenverbindung, Qualitaetsangaben,
    Relationen ueber xlink:href) werden hier bewusst ausgeklammert - die holen
    wir uns gezielt, sobald klar ist, welche Attribute das Plugin braucht.

    Codelisten-Referenzen (z.B. <anlass xlink:title="Teilung"
    xlink:href=".../AA_Anlassart/060200"/>) bekommen zusaetzlich zwei
    eigene Spalten spendiert: "<feld>_text" mit dem Klartext aus
    xlink:title und "<feld>_code" mit der Codenummer aus der URL - die
    reine URL bleibt zusaetzlich unter "<feld>" erhalten.
    """
    attrs = {}
    for child in object_elem:
        tag = local(child.tag)
        if tag in ("identifier", "lebenszeitintervall", "position", "modellart"):
            continue
        href = child.get(q(XLINK_NS, "href"))
        if href is not None and len(child) == 0:
            attrs[tag] = href  # Relation zu anderem Objekt (per OID) oder Codelisten-URL
            title = child.get(q(XLINK_NS, "title"))
            if title:
                attrs[tag + "_text"] = title
            if "/codelist/" in href:
                attrs[tag + "_code"] = href.rstrip("/").rsplit("/", 1)[-1]
        elif child.text and child.text.strip() and len(child) == 0:
            attrs[tag] = child.text.strip()
    return attrs


def strip_oid_prefix(value):
    if value and value.startswith("urn:adv:oid:"):
        return value[len("urn:adv:oid:"):]
    return value


def build_punktort_geometries(root, curve_lookup):
    """
    Manche Fachobjekte (z.B. AX_Grenzpunkt, AX_BesondererGebaeudepunkt) tragen
    selbst keine Geometrie - die Position steckt stattdessen in einem separaten
    Punktort-Objekt (AX_PunktortAG/AU/TA), das per 'istTeilVon' auf die OID des
    Fachobjekts zurueckverweist. Diese Funktion baut eine Lookup-Tabelle
    Fachobjekt-OID -> WKT auf, damit wir diese Geometrie dem Fachobjekt
    zuordnen koennen.

    Hinweis: falls mehrere Punktorte auf dieselbe OID verweisen sollten, gewinnt
    der zuletzt im Dokument gefundene (kommt in der Praxis kaum vor).
    """
    lookup = {}
    for elem in root.iter():
        ist_teil_von = None
        for child in elem:
            if local(child.tag) == "istTeilVon":
                ist_teil_von = child.get(q(XLINK_NS, "href"))
                break
        if ist_teil_von is None:
            continue
        target_oid = strip_oid_prefix(ist_teil_von)
        geom_root = find_geometry_root(elem)
        if geom_root is not None:
            lookup[target_oid] = geometry_to_wkt(geom_root, curve_lookup)
    return lookup


def parse_nas_file(path):
    """Generator: liefert NASFeature-Objekte fuer alle Insert/Replace/Delete-Aktionen."""
    tree = etree.parse(path)
    root = tree.getroot()
    curve_lookup = index_curves(root)
    punktort_lookup = build_punktort_geometries(root, curve_lookup)

    for action_elem in root.iter():
        tag = local(action_elem.tag)
        if tag not in ("Insert", "Replace", "Delete"):
            continue
        # nur direkte wfs:Transaction-Kinder betrachten, nicht z.B. verschachtelte Treffer
        if action_elem.tag != q(WFS_NS, tag):
            continue

        action = tag.lower()
        object_elem = None
        for child in action_elem:
            if local(child.tag) not in ("Filter",):
                object_elem = child
                break

        filter_elem = action_elem.find(q(FES_NS, "Filter"))
        filter_oid = None
        if filter_elem is not None:
            rid_elem = filter_elem.find(q(FES_NS, "ResourceId"))
            if rid_elem is not None:
                filter_oid = rid_elem.get("rid")

        if object_elem is not None:
            objektart = local(object_elem.tag)
            gml_id = object_elem.get(q(GML_NS, "id"))
            identifier_elem = object_elem.find(q(GML_NS, "identifier"))
            oid = strip_oid_prefix(identifier_elem.text) if identifier_elem is not None else filter_oid
            geom_root = find_geometry_root(object_elem)
            geometry_wkt = geometry_to_wkt(geom_root, curve_lookup) if geom_root is not None else None
            if geometry_wkt is None and oid in punktort_lookup:
                geometry_wkt = punktort_lookup[oid]
            attributes = extract_attributes(object_elem)
            beginnt, endet = extract_lebenszeitintervall(object_elem)
        else:
            # reines Delete: kein Objektkoerper, nur der Filter mit der OID
            objektart = None
            gml_id = None
            oid = filter_oid
            geometry_wkt = None
            attributes = {}
            beginnt, endet = None, None

        yield NASFeature(
            action=action,
            objektart=objektart,
            oid=oid,
            gml_id=gml_id,
            beginnt=beginnt,
            endet=endet,
            attributes=attributes,
            geometry_wkt=geometry_wkt,
        )
