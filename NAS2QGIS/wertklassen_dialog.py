"""
Abschnittsvergleich Flurstueck x Wertklassenflaeche zwischen zwei Staenden.

Ablauf:
  1. Aus der SCD2-Historie des NAS-GeoPackages (intern geoeffnet, nicht ins
     Projekt geladen) werden AX_Flurstueck-Zustand A und Zustand B gelesen (Vorauswahl: vorletzte und letzte Lieferung, im
     Dialog frei aenderbar). Ein vorheriger Vergleich im Reiter 'Vergleich'
     ist NICHT noetig.
  2. Vorfilter: Nur Flurstuecke, die neu sind, entfallen sind oder deren
     Geometrie sich geaendert hat, werden verschnitten. Da die
     Wertklassenflaechen in beiden Zustaenden identisch sind, koennen sich
     Abschnitte nur dort unterscheiden - das Ergebnis ist also dasselbe wie
     bei einer Vollverschneidung, nur um Groessenordnungen schneller.
  3. Fuer diese Flurstuecke werden in A und in B Abschnitte gebildet
     (Flurstueck x Wertklassenflaeche) und ueber das Schluesselpaar
     (Flurstuecks-OID, Wertklassenflaeche) verglichen.
  4. Die ROHDATEN (alle Paare mit Flaeche A / Flaeche B) werden auf der
     Plugin-Instanz gehalten. Die vom Nutzer einstellbaren Filter
     (Mindest-Flaechendifferenz, Mindestgroesse) wirken NUR in der
     Klassifizierung - eine Filteraenderung braucht keine neue Verschneidung.

Die Wertklassenflaechen (AB-Wertklassenflaechen.gpkg) werden vom Nutzer ueber
das lefistogeopackage-Plugin erzeugt - diese Logik wird hier bewusst NICHT
nachgebaut. Gesucht wird: 1. bereits im Projekt geladen, 2. im Ordner des
NAS-GeoPackages, 3. Nachfrage beim Nutzer.
"""

import csv
import os

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QTimer, QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsProject,
    QgsSettings,
    QgsSpatialIndex,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .styling import ABSCHNITT_KATEGORIEN, style_abschnitt_layer
from .vergleich_dialog import (
    _datum_de,
    _lieferungen_lesen,
    _pfad_normiert,
    _zustand_ausdruck,
    oeffne_nas_tabelle,
    projekt_crs_sicherstellen,
)

WERTKLASSEN_TABELLE = "AB-Wertklassenflaechen"
WERTKLASSEN_DATEINAME = "AB-Wertklassenflaechen.gpkg"
WERTKLASSEN_OID_FELD = "uuid"
WERTKLASSEN_WEKL_FELD = "wekl"
WERTKLASSEN_NUNK_FELD = "nunk"

FLURSTUECK_TABELLE = "AX_Flurstueck"
# Erstes vorhandene Feld wird als Flurstueckskennzeichen angezeigt (nur Anzeige,
# der Abgleich laeuft immer ueber die OID). Fehlt es, bleibt die Anzeige leer.
FLURSTUECK_KENNZEICHEN_FELDER = ("flurstueckskennzeichen",)

# Technische Untergrenze gegen reines Rundungsrauschen der Verschneidung
# (1 cm^2). Bewusst fest und winzig - alle fachlichen Schwellen sind
# Nutzerfilter und wirken erst in der Klassifizierung.
TECHNISCHE_MIN_FLAECHE = 0.0001

SETTINGS_MIN_DELTA = "NAS2QGIS/wertklassen_min_delta"
SETTINGS_MIN_FLAECHE = "NAS2QGIS/wertklassen_min_flaeche"
SETTINGS_SPLITTER = "NAS2QGIS/wertklassen_splitter_zeigen"
STANDARD_MIN_DELTA = 1.0
STANDARD_MIN_FLAECHE = 1.0

STATUS_TEXT = {wert: text for wert, text, _, _ in ABSCHNITT_KATEGORIEN}
STATUS_FARBE = {wert: kontur for wert, _, kontur, _ in ABSCHNITT_KATEGORIEN}


# --------------------------------------------------------------------------
# Datenquellen finden
# --------------------------------------------------------------------------

def _finde_wertklassen_layer():
    """Geladener Layer, dessen Quelltabelle WERTKLASSEN_TABELLE heisst -
    unabhaengig davon, wie er im Projekt umbenannt wurde."""
    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and f"layername={WERTKLASSEN_TABELLE}" in layer.source():
            return layer
    return None


def _oeffne_wertklassen_gpkg(pfad):
    """Oeffnet die Wertklassenflaechen intern (NICHT ins Projekt geladen)."""
    layer = QgsVectorLayer(f"{pfad}|layername={WERTKLASSEN_TABELLE}", "wertklassen", "ogr")
    return layer if layer.isValid() else None


def _beschaffe_wertklassen_layer(parent, nas_gpkg):
    """1. schon im Projekt, 2. neben dem NAS-GeoPackage, 3. Nachfrage.
    None = abgebrochen/nicht verfuegbar (Meldung wurde bereits gezeigt)."""
    layer = _finde_wertklassen_layer()
    if layer is not None:
        return layer

    kandidat = os.path.join(os.path.dirname(nas_gpkg), WERTKLASSEN_DATEINAME) if nas_gpkg else None
    if kandidat and os.path.exists(kandidat):
        layer = _oeffne_wertklassen_gpkg(kandidat)
        if layer is not None:
            return layer
        QMessageBox.warning(
            parent, "Wertklassenflächen nicht lesbar",
            f"Die Datei\n{kandidat}\nwurde gefunden, enthält aber keine lesbare "
            f"Tabelle '{WERTKLASSEN_TABELLE}'."
        )
        return None

    ort = os.path.dirname(kandidat) if kandidat else "dem Ordner des NAS-GeoPackages"
    antwort = QMessageBox.question(
        parent, "Wertklassenflächen nicht gefunden",
        f"Im Ordner\n{ort}\nliegt keine Datei '{WERTKLASSEN_DATEINAME}'.\n\n"
        "Bitte die Wertklassenflächen zuvor mit dem lefistogeopackage-Plugin "
        "erzeugen und dort ablegen.\n\nJetzt manuell eine Datei auswählen?"
    )
    if antwort != QMessageBox.Yes:
        return None
    pfad, _ = QFileDialog.getOpenFileName(
        parent, "Wertklassenflächen-GeoPackage wählen",
        os.path.dirname(kandidat) if kandidat else "", "GeoPackage (*.gpkg)"
    )
    if not pfad:
        return None
    layer = _oeffne_wertklassen_gpkg(pfad)
    if layer is None:
        QMessageBox.warning(
            parent, "Wertklassenflächen nicht lesbar",
            f"Die gewählte Datei enthält keine lesbare Tabelle '{WERTKLASSEN_TABELLE}'."
        )
    return layer


# --------------------------------------------------------------------------
# Rechenphase (einmalig, teuer)
# --------------------------------------------------------------------------

def _sichere_geom(geom):
    if geom is None or geom.isEmpty():
        return None
    try:
        bereinigt = geom.buffer(0, 8)
    except Exception:
        bereinigt = None
    if bereinigt is None or bereinigt.isEmpty():
        bereinigt = geom.makeValid()
    if bereinigt is None or bereinigt.isEmpty():
        return None
    return bereinigt


def _nur_flaechen(geom):
    """Verschneidungen koennen GeometryCollections mit Linien/Punkten
    (reine Beruehrungen) liefern - nur die Flaechenanteile behalten."""
    if geom is None or geom.isEmpty():
        return None
    if geom.type() == QgsWkbTypes.PolygonGeometry:
        return geom
    teile = [g for g in geom.asGeometryCollection() if g.type() == QgsWkbTypes.PolygonGeometry]
    if not teile:
        return None
    return QgsGeometry.collectGeometry(teile)


def _wertklassen_einlesen(wk_layer, ziel_crs):
    """Wertklassenflaechen einmalig einlesen, ins CRS der Flurstuecke
    transformieren (dort wird auch die Flaeche gemessen) und indexieren."""
    transform = None
    if wk_layer.crs().isValid() and ziel_crs.isValid() and wk_layer.crs() != ziel_crs:
        transform = QgsCoordinateTransform(wk_layer.crs(), ziel_crs, QgsProject.instance())

    felder = wk_layer.fields()
    oid_idx = felder.indexOf(WERTKLASSEN_OID_FELD)
    wekl_idx = felder.indexOf(WERTKLASSEN_WEKL_FELD)
    nunk_idx = felder.indexOf(WERTKLASSEN_NUNK_FELD)

    geoms, attrs = {}, {}
    index = QgsSpatialIndex()
    for feat in wk_layer.getFeatures():
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        if transform is not None:
            geom = QgsGeometry(geom)
            geom.transform(transform)
        geom = _sichere_geom(geom)
        if geom is None:
            continue
        geoms[feat.id()] = geom
        nunk = feat[nunk_idx] if nunk_idx >= 0 else None
        wekl = feat[wekl_idx] if wekl_idx >= 0 else None
        attrs[feat.id()] = {
            "uuid": str(feat[oid_idx]) if oid_idx >= 0 else str(feat.id()),
            "nutzung_wekl": " ".join(str(t) for t in (nunk, wekl) if t not in (None, "")),
        }
        indexierbar = QgsFeature(feat.id())
        indexierbar.setGeometry(geom)
        index.addFeature(indexierbar)
    return geoms, attrs, index


def _flurstuecke_lesen(layer, iso):
    """{oid: (rohgeometrie, kennzeichen)} fuer den Zustand zum Zeitpunkt iso."""
    kennz_feld = next((f for f in FLURSTUECK_KENNZEICHEN_FELDER if layer.fields().indexOf(f) >= 0), None)
    ergebnis = {}
    anfrage = QgsFeatureRequest().setFilterExpression(_zustand_ausdruck(iso))
    for f in layer.getFeatures(anfrage):
        kennz = f[kennz_feld] if kennz_feld else None
        ergebnis[f["oid"]] = (f.geometry(), "" if kennz in (None, "") else str(kennz))
    return ergebnis


def _abschnitte_bilden(flst_geom, wk_geoms, index):
    """{wk_fid: abschnitt_geom} fuer ein Flurstueck."""
    abschnitte = {}
    for wk_fid in index.intersects(flst_geom.boundingBox()):
        wk_geom = wk_geoms.get(wk_fid)
        if wk_geom is None or not wk_geom.intersects(flst_geom):
            continue
        try:
            schnitt = _nur_flaechen(flst_geom.intersection(wk_geom))
        except Exception:
            continue
        if schnitt is None or schnitt.area() < TECHNISCHE_MIN_FLAECHE:
            continue
        abschnitte[wk_fid] = schnitt
    return abschnitte


def _berechne_abschnitte(flst_layer, iso_a, iso_b, wk_layer, fortschritt=None):
    """Liefert (rohdaten, anzahl_geaenderte_flurstuecke) oder None bei Abbruch.

    rohdaten: Liste von Dicts je Abschnittspaar mit flst_oid, kennzeichen,
    wk_uuid, nutzung_wekl, flaeche_a, flaeche_b (None = Abschnitt existiert in
    dem Zustand nicht), geom_a, geom_b, flst_geom. Ungefiltert - die
    Nutzerschwellen wirken erst in _klassifizieren()."""
    crs = flst_layer.crs()
    wk_geoms, wk_attrs, index = _wertklassen_einlesen(wk_layer, crs)

    zustand_a = _flurstuecke_lesen(flst_layer, iso_a)
    zustand_b = _flurstuecke_lesen(flst_layer, iso_b)

    # Vorfilter: exakter Geometrievergleich. Unabhaengig von den
    # Nutzerschwellen - sonst wuerde ein spaeter gesenkter Schwellwert Faelle
    # nicht finden, die gar nicht berechnet wurden.
    relevante = []
    for oid in set(zustand_a) | set(zustand_b):
        a, b = zustand_a.get(oid), zustand_b.get(oid)
        if a is None or b is None:
            relevante.append(oid)
            continue
        ga, gb = a[0], b[0]
        if ga is None or gb is None or ga.isEmpty() or gb.isEmpty():
            if (ga is None or ga.isEmpty()) != (gb is None or gb.isEmpty()):
                relevante.append(oid)
            continue
        if not ga.equals(gb):
            relevante.append(oid)

    if fortschritt is not None:
        fortschritt.setMaximum(max(len(relevante), 1))

    rohdaten = []
    for i, oid in enumerate(relevante):
        if fortschritt is not None:
            fortschritt.setValue(i)
            if fortschritt.wasCanceled():
                return None

        a, b = zustand_a.get(oid), zustand_b.get(oid)
        geom_a = _sichere_geom(a[0]) if a else None
        geom_b = _sichere_geom(b[0]) if b else None
        abschnitte_a = _abschnitte_bilden(geom_a, wk_geoms, index) if geom_a else {}
        abschnitte_b = _abschnitte_bilden(geom_b, wk_geoms, index) if geom_b else {}
        kennzeichen = (b or a)[1]
        flst_geom = geom_b or geom_a

        for wk_fid in set(abschnitte_a) | set(abschnitte_b):
            ab_a, ab_b = abschnitte_a.get(wk_fid), abschnitte_b.get(wk_fid)
            rohdaten.append({
                "flst_oid": oid,
                "kennzeichen": kennzeichen,
                "wk_uuid": wk_attrs[wk_fid]["uuid"],
                "nutzung_wekl": wk_attrs[wk_fid]["nutzung_wekl"],
                "flaeche_a": ab_a.area() if ab_a else None,
                "flaeche_b": ab_b.area() if ab_b else None,
                "geom_a": ab_a,
                "geom_b": ab_b,
                "flst_geom": flst_geom,
            })

    if fortschritt is not None:
        fortschritt.setValue(fortschritt.maximum())

    rohdaten.sort(key=lambda r: (r["kennzeichen"] or r["flst_oid"], r["wk_uuid"]))
    return rohdaten, len(relevante)


# --------------------------------------------------------------------------
# Klassifizierungsphase (bei jeder Filteraenderung, billig)
# --------------------------------------------------------------------------

def _klassifizieren(rohdaten, min_delta, min_flaeche, splitter_zeigen):
    """Liefert (sichtbare Eintraege, Zaehler je Status inkl. 'unveraendert'
    und ausgeblendeter Splitter). Reine Zahlenvergleiche, keine Geometrie."""
    sichtbar = []
    zaehler = {wert: 0 for wert in STATUS_TEXT}
    zaehler["unveraendert"] = 0
    for r in rohdaten:
        a, b = r["flaeche_a"], r["flaeche_b"]
        if a is not None and b is not None:
            delta = b - a
            if abs(delta) < min_delta:
                zaehler["unveraendert"] += 1
                continue
            status = "veraendert"
        elif b is not None:
            delta = b
            status = "neu" if b >= min_flaeche else "splitter_neu"
        else:
            delta = -a
            status = "entfallen" if a >= min_flaeche else "splitter_entfallen"
        zaehler[status] += 1
        if status.startswith("splitter") and not splitter_zeigen:
            continue
        sichtbar.append(dict(r, status=status, delta=delta))
    return sichtbar, zaehler


def _flaeche_text(wert, vorzeichen=False):
    if wert is None:
        return "–"
    text = f"{wert:+.2f}" if vorzeichen else f"{wert:.2f}"
    return text.replace(".", ",")


# --------------------------------------------------------------------------
# Einstieg
# --------------------------------------------------------------------------

def oeffne_wertklassen_dialog(iface, plugin):
    """Oeffnet das Fenster - oder holt ein bereits offenes nach vorne."""
    vorhanden = getattr(plugin, "_wertklassen_dialog", None)
    if vorhanden is not None and not sip.isdeleted(vorhanden) and vorhanden.isVisible():
        vorhanden.raise_()
        vorhanden.activateWindow()
        return vorhanden

    # Elternfenster = QGIS-Hauptfenster: bleibt sichtbar, wenn der
    # Hauptdialog geschlossen wird.
    dlg = WertklassenDialog(iface, plugin, iface.mainWindow())
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    plugin._wertklassen_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


class WertklassenDialog(QDialog):
    SPALTEN = ["Flurstück / Wertklassenfläche", "Nutzung Wertklasse", "Status",
               "Fläche A [m²]", "Fläche B [m²]", "Δ [m²]"]

    def __init__(self, iface, plugin, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin = plugin
        self.gpkg_pfad = None
        self._flst_layer = None  # intern geoeffnete AX_Flurstueck-Tabelle
        self._crs = QgsCoordinateReferenceSystem()
        self.setWindowTitle("Wertklassen-Abschnitte vergleichen")
        self.setMinimumSize(820, 640)

        layout = QVBoxLayout(self)

        # --- Datengrundlage -------------------------------------------------
        grundlage = QGroupBox("Datengrundlage")
        form = QFormLayout(grundlage)
        gpkg_row = QHBoxLayout()
        self.gpkg_label = QLabel()
        self.gpkg_label.setWordWrap(True)
        gpkg_button = QPushButton("Auswählen...")
        gpkg_button.clicked.connect(self._gpkg_waehlen)
        gpkg_row.addWidget(self.gpkg_label, stretch=1)
        gpkg_row.addWidget(gpkg_button)
        self.zeitpunkt_a_combo = QComboBox()
        self.zeitpunkt_b_combo = QComboBox()
        form.addRow("NAS-GeoPackage:", gpkg_row)
        form.addRow("Zeitpunkt A (älterer Stand):", self.zeitpunkt_a_combo)
        form.addRow("Zeitpunkt B (neuerer Stand):", self.zeitpunkt_b_combo)
        self.berechnen_button = QPushButton("Abschnitte berechnen")
        self.berechnen_button.clicked.connect(self._berechnen)
        form.addRow(self.berechnen_button)
        layout.addWidget(grundlage)

        # --- Filter ----------------------------------------------------------
        filter_box = QGroupBox("Filter (wirken sofort, ohne neue Berechnung)")
        filter_form = QFormLayout(filter_box)
        self.min_delta_spin = QDoubleSpinBox()
        self.min_flaeche_spin = QDoubleSpinBox()
        for spin in (self.min_delta_spin, self.min_flaeche_spin):
            spin.setDecimals(2)
            spin.setRange(0.0, 100000.0)
            spin.setSingleStep(0.5)
            spin.setSuffix(" m²")
        self.splitter_check = QCheckBox("Splitter unter der Mindestgröße anzeigen")
        filter_form.addRow("Mindest-Flächendifferenz (veränderte Abschnitte):", self.min_delta_spin)
        filter_form.addRow("Mindestgröße (neue/entfallene Abschnitte):", self.min_flaeche_spin)
        filter_form.addRow(self.splitter_check)
        layout.addWidget(filter_box)

        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # --- Ergebnis --------------------------------------------------------
        self.baum = QTreeWidget()
        self.baum.setColumnCount(len(self.SPALTEN))
        self.baum.setHeaderLabels(self.SPALTEN)
        self.baum.itemDoubleClicked.connect(self._bei_doppelklick_zoomen)
        layout.addWidget(self.baum)

        button_row = QHBoxLayout()
        for text, slot in (
            ("Auf Auswahl zoomen", self._zoomen),
            ("Auswahl aufleuchten lassen", self._aufleuchten_lassen),
            ("OID/UUID kopieren", self._id_kopieren),
            ("Exportieren...", self._exportieren),
            ("Schließen", self.close),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            button_row.addWidget(button)
        layout.addLayout(button_row)

        # Filter entprellen: Tippen im Zahlenfeld loest nicht bei jeder
        # Ziffer einen Neuaufbau aus.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self._filter_anwenden)

        self._schwellen_laden()
        self.min_delta_spin.valueChanged.connect(self._filter_timer.start)
        self.min_flaeche_spin.valueChanged.connect(self._filter_timer.start)
        self.splitter_check.toggled.connect(self._filter_timer.start)

        # Gemeinsames GeoPackage mit Import-/Vergleich-Reiter; ein letztes
        # Ergebnis wird nur wiederhergestellt, wenn es zu dieser Datei gehoert.
        analyse = getattr(self.plugin, "wertklassen_analyse", None)
        pfad = getattr(self.plugin, "letztes_gpkg", None) or (analyse or {}).get("gpkg_pfad")
        self._quelle_setzen(pfad)
        if analyse and _pfad_normiert(analyse["gpkg_pfad"]) == _pfad_normiert(pfad):
            self._gespeichertes_ergebnis_anzeigen()

    # ------------------------------------------------------------------
    # Datengrundlage
    # ------------------------------------------------------------------

    def _gpkg_waehlen(self):
        start_ordner = os.path.dirname(self.gpkg_pfad) if self.gpkg_pfad else ""
        pfad, _ = QFileDialog.getOpenFileName(
            self, "NAS-GeoPackage wählen", start_ordner, "GeoPackage (*.gpkg)"
        )
        if pfad:
            self.plugin.letztes_gpkg = pfad  # gilt auch fuer Import-/Vergleich-Reiter
            self._quelle_setzen(pfad)

    def _quelle_setzen(self, pfad):
        self.gpkg_pfad = pfad
        self.gpkg_label.setText(pfad or "(noch nicht gewählt)")
        self._flst_layer = None
        self.zeitpunkt_a_combo.clear()
        self.zeitpunkt_b_combo.clear()
        self.berechnen_button.setEnabled(False)

        if not pfad or not os.path.exists(pfad):
            self.info_label.setText("Bitte ein NAS-GeoPackage wählen.")
            return
        self._flst_layer = oeffne_nas_tabelle(pfad, FLURSTUECK_TABELLE)
        if self._flst_layer is None:
            self.info_label.setText(
                f"Das GeoPackage enthält keine historisierte Tabelle '{FLURSTUECK_TABELLE}'."
            )
            return

        lieferungen = _lieferungen_lesen(pfad)
        for dateiname, abgabe_ende in lieferungen:
            label = f"{_datum_de(abgabe_ende)}  ({dateiname})"
            self.zeitpunkt_a_combo.addItem(label, abgabe_ende)
            self.zeitpunkt_b_combo.addItem(label, abgabe_ende)
        if len(lieferungen) < 2:
            self.info_label.setText(
                "Für einen Abschnittsvergleich werden mindestens zwei eingespielte "
                "Lieferungen benötigt."
            )
            return
        # Vorauswahl: vorletzte und letzte Lieferung
        self.zeitpunkt_a_combo.setCurrentIndex(len(lieferungen) - 2)
        self.zeitpunkt_b_combo.setCurrentIndex(len(lieferungen) - 1)
        self.berechnen_button.setEnabled(True)
        self.info_label.setText("")

    def _gespeichertes_ergebnis_anzeigen(self):
        """Beim erneuten Oeffnen das letzte Ergebnis wiederherstellen - ohne
        neue Verschneidung."""
        analyse = self.plugin.wertklassen_analyse
        iso_a, iso_b = analyse["zeitraum"]
        for combo, iso in ((self.zeitpunkt_a_combo, iso_a), (self.zeitpunkt_b_combo, iso_b)):
            idx = combo.findData(iso)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self._filter_anwenden()

    # ------------------------------------------------------------------
    # Schwellen
    # ------------------------------------------------------------------

    def _schwellen_laden(self):
        s = QgsSettings()
        self.min_delta_spin.setValue(float(s.value(SETTINGS_MIN_DELTA, STANDARD_MIN_DELTA)))
        self.min_flaeche_spin.setValue(float(s.value(SETTINGS_MIN_FLAECHE, STANDARD_MIN_FLAECHE)))
        self.splitter_check.setChecked(str(s.value(SETTINGS_SPLITTER, "false")).lower() == "true")

    def _schwellen_speichern(self):
        s = QgsSettings()
        s.setValue(SETTINGS_MIN_DELTA, self.min_delta_spin.value())
        s.setValue(SETTINGS_MIN_FLAECHE, self.min_flaeche_spin.value())
        s.setValue(SETTINGS_SPLITTER, "true" if self.splitter_check.isChecked() else "false")

    # ------------------------------------------------------------------
    # Berechnen
    # ------------------------------------------------------------------

    def _berechnen(self):
        layer = self._flst_layer
        if layer is None:
            return
        iso_a = self.zeitpunkt_a_combo.currentData()
        iso_b = self.zeitpunkt_b_combo.currentData()
        if iso_a == iso_b:
            QMessageBox.warning(self, "Fehler", "Zeitpunkt A und B sind identisch.")
            return
        if iso_a > iso_b:
            iso_a, iso_b = iso_b, iso_a

        gpkg_pfad = self.gpkg_pfad
        wk_layer = _beschaffe_wertklassen_layer(self, gpkg_pfad)
        if wk_layer is None:
            return

        fortschritt = QProgressDialog("Abschnitte werden verschnitten...", "Abbrechen", 0, 1, self)
        fortschritt.setWindowTitle("Wertklassen-Abschnitte")
        fortschritt.setWindowModality(Qt.WindowModal)
        fortschritt.setMinimumDuration(500)
        try:
            ergebnis = _berechne_abschnitte(layer, iso_a, iso_b, wk_layer, fortschritt)
        finally:
            fortschritt.close()
        if ergebnis is None:
            self.iface.messageBar().pushInfo("NAS2QGIS", "Abschnittsberechnung abgebrochen.")
            return
        rohdaten, anzahl_flurstuecke = ergebnis

        alt = getattr(self.plugin, "wertklassen_analyse", None) or {}
        self.plugin.wertklassen_analyse = {
            "gpkg_pfad": gpkg_pfad,
            "zeitraum": (iso_a, iso_b),
            "crs": layer.crs(),
            "rohdaten": rohdaten,
            "anzahl_flurstuecke": anzahl_flurstuecke,
            "layer": alt.get("layer"),  # wird wiederverwendet statt verdoppelt
        }
        self._filter_anwenden()

    # ------------------------------------------------------------------
    # Klassifizieren + Anzeige
    # ------------------------------------------------------------------

    def _filter_anwenden(self):
        self._filter_timer.stop()
        self._schwellen_speichern()
        analyse = getattr(self.plugin, "wertklassen_analyse", None)
        if not analyse:
            return
        sichtbar, zaehler = _klassifizieren(
            analyse["rohdaten"],
            self.min_delta_spin.value(),
            self.min_flaeche_spin.value(),
            self.splitter_check.isChecked(),
        )
        self._layer_aktualisieren(analyse, sichtbar)
        self._baum_aufbauen(sichtbar, analyse["crs"])
        self._zusammenfassung(analyse, zaehler)

    def _layer_aktualisieren(self, analyse, sichtbar):
        iso_a, iso_b = analyse["zeitraum"]
        name = f"Wertklassen-Abschnitte {iso_a[:10]} bis {iso_b[:10]}"
        layer = analyse.get("layer")
        if layer is None or sip.isdeleted(layer) or QgsProject.instance().mapLayer(layer.id()) is None:
            layer = QgsVectorLayer(f"MultiPolygon?crs={analyse['crs'].authid()}", name, "memory")
            felder = QgsFields()
            for feldname, typ in (
                ("status", QVariant.String), ("flst_oid", QVariant.String),
                ("flst_kennz", QVariant.String), ("wk_uuid", QVariant.String),
                ("nutzung_wekl", QVariant.String), ("flaeche_a", QVariant.Double),
                ("flaeche_b", QVariant.Double), ("delta", QVariant.Double),
            ):
                felder.append(QgsField(feldname, typ))
            layer.dataProvider().addAttributes(felder)
            layer.updateFields()
            style_abschnitt_layer(layer)
            projekt_crs_sicherstellen(analyse["crs"])
            QgsProject.instance().addMapLayer(layer)
            analyse["layer"] = layer
        layer.setName(name)

        features = []
        for e in sichtbar:
            geom = e["geom_a"] if e["status"] in ("entfallen", "splitter_entfallen") else e["geom_b"]
            if geom is None:
                continue
            geom = QgsGeometry(geom)
            geom.convertToMultiType()
            feat = QgsFeature(layer.fields())
            feat.setGeometry(geom)
            feat.setAttributes([
                e["status"], e["flst_oid"], e["kennzeichen"], e["wk_uuid"], e["nutzung_wekl"],
                e["flaeche_a"], e["flaeche_b"], e["delta"],
            ])
            features.append(feat)

        provider = layer.dataProvider()
        provider.truncate()
        provider.addFeatures(features)
        layer.updateExtents()
        layer.triggerRepaint()

    def _baum_aufbauen(self, sichtbar, crs):
        self._crs = crs
        self.baum.clear()
        gruppen = {}
        for e in sichtbar:
            gruppen.setdefault(e["flst_oid"], []).append(e)

        for oid, eintraege in gruppen.items():
            erster = eintraege[0]
            n = len(eintraege)
            kopf = f"{erster['kennzeichen']}  –  " if erster["kennzeichen"] else ""
            top = QTreeWidgetItem([f"{kopf}oid={oid}  ({n} Abschnitt{'e' if n != 1 else ''})"])
            top.setData(0, Qt.UserRole, {
                "art": "flurstueck", "oid": oid, "kennzeichen": erster["kennzeichen"],
                "geometrien": [erster["flst_geom"]],
            })
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)

            for e in eintraege:
                kind = QTreeWidgetItem([
                    e["wk_uuid"], e["nutzung_wekl"], STATUS_TEXT[e["status"]],
                    _flaeche_text(e["flaeche_a"]), _flaeche_text(e["flaeche_b"]),
                    _flaeche_text(e["delta"], vorzeichen=True),
                ])
                kind.setForeground(2, QColor(*(int(v) for v in STATUS_FARBE[e["status"]].split(","))))
                for spalte in (3, 4, 5):
                    kind.setTextAlignment(spalte, Qt.AlignRight | Qt.AlignVCenter)
                kind.setData(0, Qt.UserRole, {
                    "art": "abschnitt", "uuid": e["wk_uuid"],
                    "geometrien": [g for g in (e["geom_a"], e["geom_b"]) if g is not None],
                })
                top.addChild(kind)
            self.baum.addTopLevelItem(top)

        self.baum.expandAll()
        for spalte in range(len(self.SPALTEN)):
            self.baum.resizeColumnToContents(spalte)

    def _zusammenfassung(self, analyse, zaehler):
        iso_a, iso_b = analyse["zeitraum"]
        splitter = zaehler["splitter_neu"] + zaehler["splitter_entfallen"]
        splitter_text = (
            f"{splitter} Splitter" + ("" if self.splitter_check.isChecked() else " (ausgeblendet)")
        )
        self.info_label.setText(
            f"<b>Ergebnis {_datum_de(iso_a)} → {_datum_de(iso_b)}:</b> "
            f"{analyse['anzahl_flurstuecke']} geometrisch geänderte Flurstücke · "
            f"{zaehler['neu']} neu · {zaehler['entfallen']} entfallen · "
            f"{zaehler['veraendert']} verändert · {splitter_text} · "
            f"{zaehler['unveraendert']} unter Mindest-Flächendifferenz"
        )

    # ------------------------------------------------------------------
    # Aktionen
    # ------------------------------------------------------------------

    def _bei_doppelklick_zoomen(self, item, spalte):
        self.baum.setCurrentItem(item)
        self._zoomen()

    def _ausgewaehltes_element(self):
        auswahl = self.baum.selectedItems()
        if not auswahl:
            QMessageBox.information(self, "Keine Auswahl", "Bitte zuerst eine Zeile in der Liste auswählen.")
            return None
        return auswahl[0].data(0, Qt.UserRole)

    def _in_projekt_crs(self, geom):
        ziel_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if not self._crs.isValid() or self._crs == ziel_crs:
            return geom
        kopie = QgsGeometry(geom)
        kopie.transform(QgsCoordinateTransform(self._crs, ziel_crs, QgsProject.instance()))
        return kopie

    def _zoomen(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None or not eintrag["geometrien"]:
            return
        bbox = None
        for geom in eintrag["geometrien"]:
            box = self._in_projekt_crs(geom).boundingBox()
            if bbox is None:
                bbox = box
            else:
                bbox.combineExtentWith(box)
        # Mindestpuffer, damit auch sehr schmale Abschnitte sichtbar werden
        puffer = max(max(bbox.width(), bbox.height()) * 0.5, 10)
        canvas = self.iface.mapCanvas()
        canvas.setExtent(bbox.buffered(puffer))
        canvas.refresh()

    def _aufleuchten_lassen(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None or not eintrag["geometrien"]:
            return
        self.iface.mapCanvas().flashGeometries(eintrag["geometrien"], self._crs)

    def _id_kopieren(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None:
            return
        wert = eintrag["oid"] if eintrag["art"] == "flurstueck" else eintrag["uuid"]
        QApplication.clipboard().setText(str(wert))
        self.iface.messageBar().pushInfo("NAS2QGIS", f"Kopiert: {wert}")

    def _exportieren(self):
        if self.baum.topLevelItemCount() == 0:
            QMessageBox.information(self, "Nichts zu exportieren", "Die Ergebnisliste ist leer.")
            return
        pfad, _ = QFileDialog.getSaveFileName(
            self, "Als CSV exportieren", "wertklassen_abschnitte.csv", "CSV-Datei (*.csv)"
        )
        if not pfad:
            return
        if not pfad.lower().endswith(".csv"):
            pfad += ".csv"
        try:
            with open(pfad, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow([
                    "Flurstueck_OID", "Flurstueckskennzeichen", "Wertklassenflaeche_UUID",
                    "Nutzung_Wertklasse", "Status", "Flaeche_A_m2", "Flaeche_B_m2", "Delta_m2",
                ])
                for i in range(self.baum.topLevelItemCount()):
                    top = self.baum.topLevelItem(i)
                    daten = top.data(0, Qt.UserRole)
                    for j in range(top.childCount()):
                        kind = top.child(j)
                        writer.writerow(
                            [daten["oid"], daten["kennzeichen"]] + [kind.text(s) for s in range(len(self.SPALTEN))]
                        )
        except OSError as exc:
            QMessageBox.critical(self, "Fehler beim Export", str(exc))
            return
        self.iface.messageBar().pushSuccess("NAS2QGIS", f"Exportiert nach {pfad}")
