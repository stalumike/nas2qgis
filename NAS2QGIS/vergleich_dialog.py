"""
Vergleich-Reiter: vergleicht zwei Staende einer Objektart aus der
SCD2-Historie eines NAS-GeoPackages.

Quelle ist das GeoPackage selbst (Datei + Objektart-Tabelle), NICHT ein ins
Projekt geladener Layer: die Tabelle wird intern geoeffnet. Sichtbar werden
nur die daraus gebildeten Memory-Layer (Zustand A/B, Unterschiede,
Attributaenderungen).

Zusaetzlich kann der aktuelle Stand einer Objektart (bzw. das eigene
Verfahrensgebiet) zur Orientierung als normaler, gefilterter Layer geladen
werden - siehe aktuellen_stand_laden().
"""

import os
import sqlite3

from .parser import verfahrensnummer_aus_auftragsnummer
from .styling import style_diff_layer, style_layer
from .zeitpunkt_auswahl import ZeitpunktAuswahl

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)


NAME_ROLLE = Qt.UserRole + 1  # Tabellenname ohne Status-Zusatz (das Dropdown zeigt ihn mit)


def _pfad_normiert(pfad):
    return os.path.normcase(os.path.abspath(pfad)) if pfad else None


def nas_tabellen(gpkg_path):
    """Objektart-Tabellen mit Geometrie und Historisierung (gueltig_von)."""
    if not gpkg_path or not os.path.exists(gpkg_path):
        return []
    conn = sqlite3.connect(gpkg_path)
    try:
        kandidaten = [r[0] for r in conn.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features' ORDER BY table_name"
        )]
        tabellen = []
        for tabelle in kandidaten:
            spalten = {r[1] for r in conn.execute(f'PRAGMA table_info("{tabelle}")')}
            if "gueltig_von" in spalten:
                tabellen.append(tabelle)
    except sqlite3.OperationalError:
        tabellen = []
    finally:
        conn.close()
    return tabellen


def oeffne_nas_tabelle(gpkg_path, tabelle):
    """Oeffnet eine Objektart-Tabelle intern (NICHT ins Projekt geladen),
    ungefiltert mit allen Objektversionen. Layername = Tabellenname, damit
    er ueberall als Schluessel der Objektart dient. None, falls unlesbar."""
    layer = QgsVectorLayer(f"{gpkg_path}|layername={tabelle}", tabelle, "ogr")
    if not layer.isValid() or layer.fields().indexOf("gueltig_von") < 0:
        return None
    return layer


# Offizielle ALKIS-Schreibweise; gesucht wird trotzdem unabhaengig von
# Gross-/Kleinschreibung (siehe _brb_tabelle_finden).
BRB_TABELLE = "AX_BauRaumOderBodenordnungsrecht"
BRB_VERFAHRENSNUMMER_FELD = "bezeichnung"
AKTUELL_FILTER = '"gueltig_bis" IS NULL'


def eigene_verfahrensnummern(gpkg_path):
    """Verfahrensnummer(n) aus den eingespielten Lieferungen (auftragsnummer,
    Rueckfall antragsnummer - wie bei der Verfahrenspruefung im Import)."""
    try:
        conn = sqlite3.connect(gpkg_path)
        try:
            rows = conn.execute("SELECT antragsnummer, auftragsnummer FROM nas_lieferungen").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    nummern = set()
    for antragsnummer, auftragsnummer in rows:
        nr = verfahrensnummer_aus_auftragsnummer(auftragsnummer) or verfahrensnummer_aus_auftragsnummer(antragsnummer)
        if nr:
            nummern.add(nr)
    return sorted(nummern)


def _brb_tabelle_finden(gpkg_path):
    """Tatsaechlicher Tabellenname im GeoPackage (oder None)."""
    for tabelle in nas_tabellen(gpkg_path):
        if tabelle.lower() == BRB_TABELLE.lower():
            return tabelle
    return None


def _anzahl(gpkg_path, tabelle, where):
    conn = sqlite3.connect(gpkg_path)
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{tabelle}" WHERE {where}').fetchone()[0]
    except sqlite3.OperationalError:
        return 0  # z.B. Spalte bezeichnung fehlt
    finally:
        conn.close()


def verfahrensgebiet_pruefen(gpkg_path):
    """Liefert ein Dict mit:
      filter   - Filterausdruck fuer den Layer, None = nichts zu laden
      tabelle  - tatsaechlicher BRB-Tabellenname im GeoPackage
      name     - Layername
      tooltip  - Erklaerung fuer den Button (aktiv wie ausgegraut)

    1. Verfahrensnummer aus der Auftragsnummer erkannt und ein aktuelles
       BRB mit dieser bezeichnung vorhanden -> nur das eigene Verfahren.
    2. Sonst (Auftragsnummer nicht im erwarteten Muster - jedes Katasteramt
       traegt dort etwas anderes ein - oder keine passende bezeichnung)
       -> Rueckfall: alle aktuellen BRB-Objekte. Die Lieferung ist nach den
       Flurstuecken des Verfahrens zusammengestellt, das eigene
       Verfahrensgebiet ist also auf jeden Fall dabei.
    3. Kein aktuelles BRB-Objekt -> nichts zu laden (Button grau)."""
    ergebnis = {"filter": None, "tabelle": None, "name": None, "tooltip": None}
    if not gpkg_path or not os.path.exists(gpkg_path):
        ergebnis["tooltip"] = "Kein NAS-GeoPackage gewählt."
        return ergebnis
    tabelle = _brb_tabelle_finden(gpkg_path)
    if tabelle is None:
        ergebnis["tooltip"] = f"Die Lieferungen enthalten keine Objektart {BRB_TABELLE}."
        return ergebnis
    ergebnis["tabelle"] = tabelle

    nummern = eigene_verfahrensnummern(gpkg_path)
    if nummern:
        werte = ", ".join("'" + n.replace("'", "''") + "'" for n in nummern)
        eigener_filter = f'{AKTUELL_FILTER} AND TRIM("{BRB_VERFAHRENSNUMMER_FELD}") IN ({werte})'
        if _anzahl(gpkg_path, tabelle, eigener_filter):
            nr_text = ", ".join(nummern)
            ergebnis.update(
                filter=eigener_filter,
                name=f"Verfahrensgebiet {nr_text} (aktueller Stand)",
                tooltip=f"Verfahrensgebiet {nr_text} (aktueller Stand) laden und darauf zoomen",
            )
            return ergebnis

    if _anzahl(gpkg_path, tabelle, AKTUELL_FILTER) == 0:
        ergebnis["tooltip"] = "Die Lieferungen enthalten kein aktuelles Verfahrensgebiet."
        return ergebnis

    grund = (
        f"Kein Verfahrensgebiet mit der Nummer {', '.join(nummern)} gefunden"
        if nummern else "Die Verfahrensnummer ist aus den Lieferungen nicht erkennbar"
    )
    ergebnis.update(
        filter=AKTUELL_FILTER,
        name="Verfahrensgebiete (aktueller Stand)",
        tooltip=f"{grund} - es werden alle aktuellen Verfahrensgebiete geladen.",
    )
    return ergebnis


def aktuellen_stand_laden(iface, gpkg_path, tabelle, filter_ausdruck=AKTUELL_FILTER, name=None, zoomen=False):
    """Laedt eine Objektart als normalen GeoPackage-Layer, gefiltert auf den
    aktuellen Stand. Der Filter ist dynamisch: nach einem spaeteren Import
    zeigt der Layer automatisch den neuen Stand. Ist derselbe Layer schon im
    Projekt, wird er nur ausgewaehlt (kein Duplikat)."""
    name = name or f"{tabelle} (aktueller Stand)"
    ziel = _pfad_normiert(gpkg_path)
    for vorhanden in QgsProject.instance().mapLayers().values():
        if not isinstance(vorhanden, QgsVectorLayer) or vorhanden.providerType() != "ogr":
            continue
        teile = vorhanden.source().split("|")
        if (_pfad_normiert(teile[0]) == ziel and f"layername={tabelle}" in teile
                and vorhanden.subsetString() == filter_ausdruck):
            iface.setActiveLayer(vorhanden)
            iface.messageBar().pushInfo("NAS2QGIS", f"'{vorhanden.name()}' ist bereits geladen.")
            if zoomen:
                _auf_layer_zoomen(iface, vorhanden)
            return vorhanden

    layer = QgsVectorLayer(f"{gpkg_path}|layername={tabelle}", name, "ogr")
    if not layer.isValid() or not layer.setSubsetString(filter_ausdruck):
        QMessageBox.warning(iface.mainWindow(), "Fehler", f"'{tabelle}' konnte nicht geladen werden.")
        return None
    style_layer(layer, tabelle)
    projekt_crs_sicherstellen(layer.crs())
    QgsProject.instance().addMapLayer(layer)
    if zoomen:
        _auf_layer_zoomen(iface, layer)
    return layer


def _auf_layer_zoomen(iface, layer):
    canvas = iface.mapCanvas()
    ausdehnung = layer.extent()
    ziel_crs = canvas.mapSettings().destinationCrs()
    if layer.crs().isValid() and layer.crs() != ziel_crs:
        ausdehnung = QgsCoordinateTransform(layer.crs(), ziel_crs, QgsProject.instance()).transformBoundingBox(ausdehnung)
    canvas.setExtent(ausdehnung.buffered(max(ausdehnung.width(), ausdehnung.height()) * 0.05))
    canvas.refresh()


def projekt_crs_sicherstellen(crs):
    """Ersatz fuer das fruehere Setzen beim Laden der GeoPackage-Layer: In
    einem leeren Projekt das KBS der NAS-Daten uebernehmen, damit die
    Ergebnis-Layer nicht in einem zufaelligen Standard-KBS landen."""
    projekt = QgsProject.instance()
    if crs.isValid() and (not projekt.mapLayers() or not projekt.crs().isValid()):
        projekt.setCrs(crs)


def _datum_de(iso):
    """'2026-06-30T00:00:00Z' -> '30.06.2026'"""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


def _gpkg_path_from_layer(layer):
    return layer.source().split("|")[0]


def _lieferungen_lesen(gpkg_path):
    conn = sqlite3.connect(gpkg_path)
    try:
        rows = conn.execute(
            "SELECT dateiname, abgabeintervallEnde FROM nas_lieferungen ORDER BY abgabeintervallEnde"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return rows


def _zustand_ausdruck(iso_datum):
    return (
        f'"gueltig_von" <= \'{iso_datum}\' AND '
        f'("gueltig_bis" IS NULL OR "gueltig_bis" > \'{iso_datum}\')'
    )


def _memory_layer_erzeugen(name, layer, felder):
    wkb_typ = QgsWkbTypes.displayString(layer.wkbType())
    crs_auth_id = layer.crs().authid()
    mem_layer = QgsVectorLayer(f"{wkb_typ}?crs={crs_auth_id}", name, "memory")
    mem_layer.dataProvider().addAttributes(felder)
    mem_layer.updateFields()
    return mem_layer


class VergleichTab(QWidget):
    def __init__(self, iface, plugin, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.plugin = plugin
        self.blink_controller = plugin.blink_controller

        self.gpkg_pfad = None
        self._quellen = {}  # Tabellenname -> intern geoeffneter Layer (fuer self.gpkg_pfad)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("NAS-GeoPackage:"))
        gpkg_row = QHBoxLayout()
        self.gpkg_label = QLabel()
        self.gpkg_label.setWordWrap(True)
        gpkg_button = QPushButton("Auswählen...")
        gpkg_button.clicked.connect(self._gpkg_waehlen)
        self.verfahrensgebiet_button = QPushButton("Verfahrensgebiet anzeigen")
        self.verfahrensgebiet_button.clicked.connect(self._verfahrensgebiet_anzeigen)
        gpkg_row.addWidget(self.gpkg_label, stretch=1)
        gpkg_row.addWidget(self.verfahrensgebiet_button)
        gpkg_row.addWidget(gpkg_button)
        layout.addLayout(gpkg_row)

        layout.addWidget(QLabel("Objektart:"))
        objektart_row = QHBoxLayout()
        self.layer_combo = QComboBox()
        self.stand_button = QPushButton("Aktuellen Stand anzeigen")
        self.stand_button.setToolTip(
            "Lädt die gewählte Objektart mit ihrem aktuellen Stand (ohne historische "
            "Versionen) in die Karte."
        )
        self.stand_button.clicked.connect(self._aktuellen_stand_anzeigen)
        objektart_row.addWidget(self.layer_combo, stretch=1)
        objektart_row.addWidget(self.stand_button)
        layout.addLayout(objektart_row)

        self.zeitpunkte = ZeitpunktAuswahl()
        layout.addWidget(self.zeitpunkte)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Vergleichen")
        self.run_button.setEnabled(False)
        button_row.addWidget(self.run_button)
        layout.addLayout(button_row)
        # Vergleichen nur bei sinnvoller Auswahl (A aelter als B)
        self.zeitpunkte.gueltigkeit_geaendert.connect(self.run_button.setEnabled)

        self.attr_button = QPushButton("Attributänderungen anzeigen")
        self.attr_button.setEnabled(False)
        self.attr_button.clicked.connect(self._zeige_attributaenderungen)
        layout.addWidget(self.attr_button)

        wertklassen_button = QPushButton("Wertklassen-Abschnitte vergleichen...")
        wertklassen_button.clicked.connect(self._oeffne_wertklassen_dialog)
        layout.addWidget(wertklassen_button)

        self.layer_combo.currentIndexChanged.connect(self._attr_button_aktualisieren)
        self.run_button.clicked.connect(self.vergleichen)

        self.quelle_aktualisieren()
        layout.addStretch()

    def _zeige_attributaenderungen(self):
        layer = self.aktueller_layer()
        if layer is None:
            return
        self.plugin.zeige_attributaenderungen(layer.name())

    def _layername_aus_dropdown(self):
        idx = self.layer_combo.currentIndex()
        if idx < 0:
            return None
        return self.layer_combo.itemData(idx, NAME_ROLLE)

    def _attr_button_aktualisieren(self):
        """Beschriftung und Aktivierung richten sich nach dem GERADE gewaehlten
        Layer (Objektart): aktiv nur, wenn dafuer schon ein Vergleich vorliegt."""
        name = self._layername_aus_dropdown()
        if name is None:
            self.attr_button.setText("Attributänderungen anzeigen")
            self.attr_button.setEnabled(False)
            return
        self.attr_button.setText(f"Attributänderungen anzeigen ({name})")
        self.attr_button.setEnabled(name in self.plugin.vergleiche)

    def _dropdown_texte_aktualisieren(self):
        """Zeigt im Layer-Dropdown an, fuer welche Objektarten schon ein
        Vergleich vorliegt (mit Zeitraum) - ohne die Auswahl zu veraendern."""
        for i in range(self.layer_combo.count()):
            name = self.layer_combo.itemData(i, NAME_ROLLE)
            vergleich = self.plugin.vergleiche.get(name)
            if vergleich is None or _pfad_normiert(vergleich.get("gpkg_pfad")) != _pfad_normiert(self.gpkg_pfad):
                self.layer_combo.setItemText(i, name)
            else:
                a, b = vergleich["zeitraum"]
                self.layer_combo.setItemText(i, f"{name}  ✔  ({_datum_de(a)} → {_datum_de(b)})")

    def _oeffne_wertklassen_dialog(self):
        from .wertklassen_dialog import oeffne_wertklassen_dialog
        self._wertklassen_dialog = oeffne_wertklassen_dialog(self.iface, self.plugin)

    def _gpkg_waehlen(self):
        start_ordner = os.path.dirname(self.gpkg_pfad) if self.gpkg_pfad else ""
        pfad, _ = QFileDialog.getOpenFileName(
            self, "NAS-GeoPackage wählen", start_ordner, "GeoPackage (*.gpkg)"
        )
        if not pfad:
            return
        # Gilt auch fuer den Import-Reiter, damit beide auf dieselbe Datei zeigen
        self.plugin.letztes_gpkg = pfad
        self.quelle_aktualisieren()

    def quelle_aktualisieren(self):
        """Uebernimmt plugin.letztes_gpkg (gemeinsam mit dem Import-Reiter)
        und liest Objektarten und Lieferungen neu ein. Wird beim Wechsel auf
        diesen Reiter aufgerufen - so sind auch gerade erst importierte
        Lieferungen/Objektarten sofort waehlbar."""
        self.gpkg_pfad = self.plugin.letztes_gpkg
        # Interne Layer neu oeffnen: nach einem Import koennen neue Spalten
        # dazugekommen sein, die ein bereits geoeffneter Layer nicht kennt.
        self._quellen = {}
        self.gpkg_label.setText(self.gpkg_pfad or "(noch nicht gewählt)")

        vorher = self._layername_aus_dropdown()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for tabelle in nas_tabellen(self.gpkg_pfad):
            self.layer_combo.addItem(tabelle)
            self.layer_combo.setItemData(self.layer_combo.count() - 1, tabelle, NAME_ROLLE)
        self._dropdown_texte_aktualisieren()
        # Bisherige Auswahl beibehalten, sonst AX_Flurstueck vorwaehlen
        for gewuenscht in (vorher, "AX_Flurstueck"):
            idx = self.layer_combo.findData(gewuenscht, NAME_ROLLE) if gewuenscht else -1
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
                break
        self.layer_combo.blockSignals(False)
        # Kein Popup bei leerem Dropdown: dieser Reiter wird beim Oeffnen des
        # Hauptdialogs sofort aufgebaut, auch wenn der Nutzer nur importieren
        # will. Beim Klick auf "Vergleichen" kommt ggf. eine Meldung.
        self.lieferungen_aktualisieren()
        self._attr_button_aktualisieren()
        self.stand_button.setEnabled(self.layer_combo.count() > 0)
        self._verfahrensgebiet_button_aktualisieren()

    def _verfahrensgebiet_button_aktualisieren(self):
        self._verfahrensgebiet = verfahrensgebiet_pruefen(self.gpkg_pfad)
        self.verfahrensgebiet_button.setEnabled(self._verfahrensgebiet["filter"] is not None)
        self.verfahrensgebiet_button.setToolTip(self._verfahrensgebiet["tooltip"])

    def _verfahrensgebiet_anzeigen(self):
        vg = self._verfahrensgebiet
        if not vg["filter"]:
            return
        aktuellen_stand_laden(
            self.iface, self.gpkg_pfad, vg["tabelle"], vg["filter"], name=vg["name"], zoomen=True,
        )

    def _aktuellen_stand_anzeigen(self):
        tabelle = self._layername_aus_dropdown()
        if tabelle and self.gpkg_pfad:
            aktuellen_stand_laden(self.iface, self.gpkg_pfad, tabelle)

    def aktueller_layer(self):
        """Intern geoeffnete Tabelle der gewaehlten Objektart (oder None)."""
        tabelle = self._layername_aus_dropdown()
        if tabelle is None or not self.gpkg_pfad:
            return None
        layer = self._quellen.get(tabelle)
        if layer is None:
            layer = oeffne_nas_tabelle(self.gpkg_pfad, tabelle)
            if layer is not None:
                self._quellen[tabelle] = layer
        return layer

    def lieferungen_aktualisieren(self):
        """Liest die Lieferungen neu ein. Die bisherige Auswahl A/B bleibt
        erhalten, wenn beide Zeitpunkte noch existieren (z.B. beim
        Reiterwechsel mitten in einer Vergleichskette); sonst Vorauswahl
        erste und letzte Lieferung (Gesamtaenderung)."""
        vorher = (self.zeitpunkte.iso_a(), self.zeitpunkte.iso_b())
        if not self.gpkg_pfad or not os.path.exists(self.gpkg_pfad):
            self.zeitpunkte.setze_lieferungen([])
            return
        self.zeitpunkte.setze_lieferungen(_lieferungen_lesen(self.gpkg_pfad), index_a=0, index_b=-1)
        if vorher[0] and vorher[1]:
            self.zeitpunkte.setze_zeitpunkte(*vorher)

    def vergleichen(self):
        if not self.gpkg_pfad or not os.path.exists(self.gpkg_pfad):
            QMessageBox.warning(self, "Fehler", "Bitte zuerst ein NAS-GeoPackage wählen.")
            return
        if self.layer_combo.count() == 0:
            QMessageBox.warning(
                self, "Fehler", "Das GeoPackage enthält keine historisierten Objektart-Tabellen."
            )
            return
        layer = self.aktueller_layer()
        if layer is None:
            QMessageBox.warning(self, "Fehler", "Die gewählte Objektart konnte nicht geöffnet werden.")
            return
        # Der Button ist bei ungueltiger Auswahl ohnehin ausgegraut - die
        # Pruefung hier ist nur die Absicherung.
        if not self.zeitpunkte.ist_gueltig():
            return
        self._fuehre_vergleich_aus(layer, self.zeitpunkte.iso_a(), self.zeitpunkte.iso_b())

    def _fuehre_vergleich_aus(self, layer, iso_a, iso_b):
        quell_felder = layer.fields()
        vergleichs_felder = [
            f.name() for f in quell_felder if f.name() not in ("oid", "gueltig_von", "gueltig_bis", "fid")
        ]

        features_a = {
            f["oid"]: f for f in layer.getFeatures(QgsFeatureRequest().setFilterExpression(_zustand_ausdruck(iso_a)))
        }
        features_b = {
            f["oid"]: f for f in layer.getFeatures(QgsFeatureRequest().setFilterExpression(_zustand_ausdruck(iso_b)))
        }

        diff_felder = QgsFields(quell_felder)
        diff_felder.append(QgsField("vergleichsstatus", QVariant.String))

        layer_a = _memory_layer_erzeugen(f"{layer.name()} - Zustand {iso_a[:10]}", layer, quell_felder)
        layer_b = _memory_layer_erzeugen(f"{layer.name()} - Zustand {iso_b[:10]}", layer, quell_felder)
        # Beide mit identischem Stil (keine Fuellung, gleiche Farbe) - beim
        # Blinkvergleich soll nur die tatsaechliche Kantenverschiebung
        # auffallen, nicht ein Farbwechsel durch unterschiedliche Symbolik.
        style_layer(layer_a, layer.name())
        style_layer(layer_b, layer.name())
        layer_diff = _memory_layer_erzeugen(
            f"{layer.name()} - Unterschiede {iso_a[:10]} bis {iso_b[:10]}", layer, diff_felder
        )
        # Unterschiede-Layer nach vergleichsstatus einfaerben (neu/entfernt/...)
        style_diff_layer(layer_diff)

        attr_aenderungen_felder = QgsFields()
        attr_aenderungen_felder.append(QgsField("objektart", QVariant.String))
        attr_aenderungen_felder.append(QgsField("oid", QVariant.String))
        attr_aenderungen_felder.append(QgsField("feld", QVariant.String))
        attr_aenderungen_felder.append(QgsField("alter_wert", QVariant.String))
        attr_aenderungen_felder.append(QgsField("neuer_wert", QVariant.String))
        # Mit Geometrie statt "None" anlegen, damit sich in der Attributtabelle
        # der eingebaute "Auf Auswahl zoomen"-Knopf nutzen laesst - sonst
        # muesste man das betroffene Objekt muehsam von Hand in der Karte suchen.
        layer_attr = _memory_layer_erzeugen(
            f"{layer.name()} - Attributaenderungen {iso_a[:10]} bis {iso_b[:10]}", layer, attr_aenderungen_felder
        )

        layer_a.dataProvider().addFeatures(list(features_a.values()))
        layer_b.dataProvider().addFeatures(list(features_b.values()))

        anzahl_neu = anzahl_entfernt = anzahl_geaendert = 0
        diff_features = []
        attr_change_features = []
        aenderungen_je_objekt = []  # fuer das gruppierte Attributaenderungen-Fenster
        for oid in set(features_a) | set(features_b):
            fa, fb = features_a.get(oid), features_b.get(oid)

            if fa is None:
                status, basis = "neu", fb
                anzahl_neu += 1
            elif fb is None:
                status, basis = "entfernt", fa
                anzahl_entfernt += 1
            else:
                geom_a, geom_b = fa.geometry(), fb.geometry()
                if geom_a is None and geom_b is None:
                    geometrie_geaendert = False
                elif geom_a is None or geom_b is None:
                    geometrie_geaendert = True
                else:
                    geometrie_geaendert = not geom_a.equals(geom_b)

                feld_diffs = []
                for feldname in vergleichs_felder:
                    alt = fa[feldname]
                    neu = fb[feldname]
                    if alt != neu:
                        feld_diffs.append((feldname, alt, neu))

                if not geometrie_geaendert and not feld_diffs:
                    continue  # wirklich unveraendert -> nicht in die Ausgabe aufnehmen

                if geometrie_geaendert and feld_diffs:
                    status = "geometrie_und_attribute_geaendert"
                elif geometrie_geaendert:
                    status = "geometrie_geaendert"
                else:
                    status = "attribute_geaendert"
                basis = fb
                anzahl_geaendert += 1

                for feldname, alt, neu in feld_diffs:
                    attr_feat = QgsFeature(attr_aenderungen_felder)
                    attr_feat.setGeometry(basis.geometry())
                    attr_feat.setAttributes([
                        layer.name(), oid, feldname,
                        "" if alt is None else str(alt),
                        "" if neu is None else str(neu),
                    ])
                    attr_change_features.append(attr_feat)

                if feld_diffs:
                    aenderungen_je_objekt.append({
                        "objektart": layer.name(),
                        "oid": oid,
                        "geometrie": basis.geometry(),
                        "felder": [
                            (fn, "" if alt is None else str(alt), "" if neu is None else str(neu))
                            for fn, alt, neu in feld_diffs
                        ],
                    })

            neues_feature = QgsFeature(diff_felder)
            if status in ("geometrie_geaendert", "geometrie_und_attribute_geaendert"):
                neues_feature.setGeometry(fa.geometry().symDifference(fb.geometry()))
            else:
                neues_feature.setGeometry(basis.geometry())
            neues_feature.setAttributes(basis.attributes() + [status])
            diff_features.append(neues_feature)

        layer_diff.dataProvider().addFeatures(diff_features)
        layer_attr.dataProvider().addFeatures(attr_change_features)

        # Die Vergleichs-Layer eines frueheren Vergleichs derselben Objektart
        # aus dem Projekt entfernen, damit sie sich nicht immer wieder
        # verdoppeln. Vorher einen evtl. darauf laufenden Blinkvergleich stoppen.
        alt = self.plugin.vergleiche.get(layer.name())
        if alt is not None:
            self.blink_controller.stoppen()
            for schluessel in ("zustand_a_layer", "zustand_b_layer", "diff_layer", "attr_layer"):
                alter_layer = alt.get(schluessel)
                if alter_layer is not None and not sip.isdeleted(alter_layer):
                    QgsProject.instance().removeMapLayer(alter_layer.id())

        projekt_crs_sicherstellen(layer.crs())
        for l in (layer_a, layer_b, layer_diff, layer_attr):
            if hasattr(l, "updateExtents"):
                l.updateExtents()
            QgsProject.instance().addMapLayer(l)

        # Blinkvergleich fuer die beiden neuen Zustands-Layer aktivieren -
        # der Controller gehoert der Plugin-Instanz, nicht diesem Dialog,
        # und bleibt daher auch nach dem Schliessen des Fensters bestehen.
        # Die Bedienelemente dafuer liegen im Attributaenderungen-Fenster.
        self.blink_controller.layer_setzen(layer_a, layer_b)

        # Ergebnis dieses Vergleichs JE OBJEKTART merken (siehe plugin.vergleiche):
        # Attributaenderungen-Fenster und Blinkvergleich greifen darauf zu
        # (der Wertklassen-Abschnittsvergleich NICHT - der liest die Zustaende
        # selbst aus dem GeoPackage) - ein spaeterer Vergleich einer anderen
        # Objektart ueberschreibt diesen Eintrag nicht.
        self.plugin.vergleiche[layer.name()] = {
            "diff_layer": layer_diff,
            "attr_layer": layer_attr,
            "zustand_a_layer": layer_a,
            "zustand_b_layer": layer_b,
            "crs": layer.crs(),
            "gpkg_pfad": _gpkg_path_from_layer(layer),
            "aenderungen": aenderungen_je_objekt,
            "zeitraum": (iso_a, iso_b),
        }
        self._dropdown_texte_aktualisieren()
        self._attr_button_aktualisieren()

        # Immer neu aufbauen - auch bei 0 Attributaenderungen, damit kein
        # veraltetes Fenster einer anderen Objektart stehen bleibt (das neue
        # Fenster zeigt dann einen entsprechenden Leer-Hinweis).
        self.plugin.zeige_attributaenderungen(layer.name(), neu_aufbauen=True)

        self.iface.messageBar().pushSuccess(
            "NAS-Vergleich",
            f"Zustand A: {len(features_a)} | Zustand B: {len(features_b)} | "
            f"Neu: {anzahl_neu}, Entfernt: {anzahl_entfernt}, Geaendert: {anzahl_geaendert} "
            f"({len(attr_change_features)} Attributaenderungen)",
        )
