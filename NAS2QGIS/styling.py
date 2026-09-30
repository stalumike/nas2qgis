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
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsRendererCategory,
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
    "AX_Flurstueck": {"farbe": "35,35,35", "breite": 0.6},
    "AX_Gebaeude": {"farbe": "227,26,28", "breite": 0.8},
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


# Farben des Unterschiede-Layers je vergleichsstatus:
# (Wert im Feld, Legendentext, "r,g,b" Kontur, "r,g,b,a" Fuellung)
DIFF_KATEGORIEN = [
    ("neu", "neu", "46,125,50", "76,175,80,110"),
    ("entfernt", "entfernt", "198,40,40", "244,67,54,110"),
    ("geometrie_geaendert", "Geometrie geändert", "230,126,0", "255,167,38,130"),
    ("attribute_geaendert", "nur Attribute geändert", "84,110,122", "144,164,174,110"),
    ("geometrie_und_attribute_geaendert", "Geometrie und Attribute geändert", "123,31,162", "171,71,188,130"),
]


def _diff_symbol(geom_typ, kontur, fuellung):
    if geom_typ == QgsWkbTypes.PolygonGeometry:
        return QgsFillSymbol.createSimple({
            "color": fuellung,
            "outline_style": "solid",
            "outline_width": "0.7",
            "outline_color": kontur + ",255",
        })
    if geom_typ == QgsWkbTypes.LineGeometry:
        return QgsLineSymbol.createSimple({"line_width": "1.0", "line_color": kontur + ",255"})
    return QgsMarkerSymbol.createSimple({"size": "3", "color": fuellung, "outline_color": kontur + ",255"})


def style_diff_layer(layer):
    """Faerbt den Unterschiede-Layer eines Vergleichs nach dem Feld
    'vergleichsstatus' ein (neu / entfernt / Geometrie geaendert / ...). Die
    Legende erscheint dadurch automatisch im Layerbaum."""
    geom_typ = layer.geometryType()
    kategorien = [
        QgsRendererCategory(wert, _diff_symbol(geom_typ, kontur, fuellung), text)
        for wert, text, kontur, fuellung in DIFF_KATEGORIEN
    ]
    # Leerer Wert = alle uebrigen Faelle (z.B. kuenftig neue Status)
    kategorien.append(QgsRendererCategory("", _diff_symbol(geom_typ, "97,97,97", "158,158,158,110"), "sonstige"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("vergleichsstatus", kategorien))
    layer.triggerRepaint()


# Farben des Wertklassen-Abschnitte-Layers je Status (siehe wertklassen_dialog.py):
# (Wert im Feld 'status', Legendentext, "r,g,b" Kontur, "r,g,b,a" Fuellung)
ABSCHNITT_KATEGORIEN = [
    ("neu", "neuer Abschnitt", "46,125,50", "76,175,80,120"),
    ("entfallen", "entfallener Abschnitt", "198,40,40", "244,67,54,120"),
    ("veraendert", "Abschnittsfläche verändert", "230,126,0", "255,167,38,130"),
    ("splitter_neu", "Splitter (neu)", "97,97,97", "158,158,158,90"),
    ("splitter_entfallen", "Splitter (entfallen)", "97,97,97", "158,158,158,90"),
]


def style_abschnitt_layer(layer):
    """Faerbt den Wertklassen-Abschnitte-Layer nach dem Feld 'status' ein."""
    geom_typ = layer.geometryType()
    kategorien = [
        QgsRendererCategory(wert, _diff_symbol(geom_typ, kontur, fuellung), text)
        for wert, text, kontur, fuellung in ABSCHNITT_KATEGORIEN
    ]
    layer.setRenderer(QgsCategorizedSymbolRenderer("status", kategorien))
    layer.triggerRepaint()
