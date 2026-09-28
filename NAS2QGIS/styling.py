"""Vorab-Stilisierung fuer frisch geladene NAS-Layer.

Ohne das wuerde QGIS jedem Layer eine zufaellige (meist deckende)
Fuellfarbe geben - bei vielen gleichzeitig geladenen Objektarten
verdeckt das schnell alles. Stattdessen: Flaechen nur als Kontur,
duenne Linien, kleine Punktsymbole.

Farbe/Staerke lassen sich pro Objektart fest vorgeben (LAYER_STILE).
Alles, was dort nicht eingetragen ist, bekommt automatisch eine Farbe
aus der rotierenden Palette (FARBPALETTE) und die Standardwerte.
"""

from qgis.core import (
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsSingleSymbolRenderer,
    QgsWkbTypes,
)

# Rotierende Farbpalette (RGB) - fuer alle Objektarten OHNE Eintrag in
# LAYER_STILE. Bei mehr Layern als Farben faengt es wieder von vorne an.
FARBPALETTE = [
    "228,26,28",    # rot
    "55,126,184",   # blau
    "77,175,74",    # gruen
    "152,78,163",   # lila
    "255,127,0",    # orange
    "166,86,40",    # braun
    "247,129,191",  # pink
    "153,153,153",  # grau
    "0,150,150",    # tuerkis
    "202,178,0",    # oliv/gold
]

STANDARD_LINIENBREITE = 0.5   # mm, fuer Flaechen-Konturen und Linienobjekte
STANDARD_PUNKTGROESSE = 2.0   # mm, fuer Punktobjekte

# Feste Stile je Objektart (Tabellenname exakt wie im GeoPackage).
# farbe: "r,g,b" oder "r,g,b,a" (0-255). breite: Linienbreite/Punktgroesse in mm.
# Nicht eingetragene Eintraege fallen auf die Standardwerte zurueck.
LAYER_STILE = {
    # "AX_Flurstueck": {"farbe": "35,35,35", "breite": 0.6},
    "AX_BauRaumOderBodenordnungsrecht": {"farbe": "228,26,28", "breite": 2.0},
    # weitere Objektarten hier ergaenzen, z.B.:
    # "AX_Grenzpunkt": {"farbe": "0,0,0", "breite": 1.5},
}


def farbe_fuer_index(index):
    return FARBPALETTE[index % len(FARBPALETTE)] + ",255"


def _mit_alpha(farbe):
    """Ergaenzt fehlenden Alpha-Kanal ('r,g,b' -> 'r,g,b,255')."""
    teile = farbe.split(",")
    if len(teile) == 3:
        return farbe + ",255"
    return farbe


def _stil_ermitteln(tabellenname, index):
    vorgabe = LAYER_STILE.get(tabellenname, {})
    farbe = _mit_alpha(vorgabe["farbe"]) if "farbe" in vorgabe else farbe_fuer_index(index)
    breite = vorgabe.get("breite", STANDARD_LINIENBREITE)
    groesse = vorgabe.get("groesse", STANDARD_PUNKTGROESSE)
    return farbe, breite, groesse


def style_layer(layer, tabellenname, index=0):
    """tabellenname: Objektart/Tabellenname (fuer den Lookup in LAYER_STILE).
    index: Position in der Ladereihenfolge (nur relevant als Fallback fuer
    die Palettenfarbe, falls kein fester Stil hinterlegt ist)."""
    farbe, breite, groesse = _stil_ermitteln(tabellenname, index)
    geom_typ = layer.geometryType()

    if geom_typ == QgsWkbTypes.PolygonGeometry:
        symbol = QgsFillSymbol.createSimple({
            "style": "no",              # keine Flaechenfuellung
            "outline_style": "solid",
            "outline_width": str(breite),
            "outline_color": farbe,
        })
    elif geom_typ == QgsWkbTypes.LineGeometry:
        symbol = QgsLineSymbol.createSimple({
            "line_width": str(breite),
            "line_color": farbe,
        })
    elif geom_typ == QgsWkbTypes.PointGeometry:
        symbol = QgsMarkerSymbol.createSimple({
            "size": str(groesse),
            "color": farbe,
        })
    else:
        return

    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()
