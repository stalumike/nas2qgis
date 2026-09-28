import sqlite3

from .styling import style_layer

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)


NAME_ROLLE = Qt.UserRole + 1  # Layername ohne Status-Zusatz (das Dropdown zeigt ihn mit)


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

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Layer (aus dem NAS-GeoPackage):"))
        self.layer_combo = QComboBox()
        layout.addWidget(self.layer_combo)

        layout.addWidget(QLabel("Zeitpunkt A (aelterer Stand):"))
        self.lieferung_a_combo = QComboBox()
        layout.addWidget(self.lieferung_a_combo)

        layout.addWidget(QLabel("Zeitpunkt B (neuerer Stand):"))
        self.lieferung_b_combo = QComboBox()
        layout.addWidget(self.lieferung_b_combo)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Vergleichen")
        button_row.addWidget(self.run_button)
        layout.addLayout(button_row)

        self.attr_button = QPushButton("Attributänderungen anzeigen")
        self.attr_button.setEnabled(False)
        self.attr_button.clicked.connect(self._zeige_attributaenderungen)
        layout.addWidget(self.attr_button)

        wertklassen_button = QPushButton("Betroffene Wertklassenflächen ermitteln...")
        wertklassen_button.clicked.connect(self._oeffne_wertklassen_dialog)
        layout.addWidget(wertklassen_button)

        self.layer_combo.currentIndexChanged.connect(self.lieferungen_aktualisieren)
        self.layer_combo.currentIndexChanged.connect(self._attr_button_aktualisieren)
        self.run_button.clicked.connect(self.vergleichen)

        self.layer_liste_befuellen()
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
            if vergleich is None:
                self.layer_combo.setItemText(i, name)
            else:
                a, b = vergleich["zeitraum"]
                self.layer_combo.setItemText(i, f"{name}  ✔  ({_datum_de(a)} → {_datum_de(b)})")

    def _oeffne_wertklassen_dialog(self):
        from .wertklassen_dialog import oeffne_wertklassen_dialog
        self._wertklassen_dialog = oeffne_wertklassen_dialog(self.iface, self.plugin)

    def layer_liste_befuellen(self):
        vorher = self._layername_aus_dropdown()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for layer in QgsProject.instance().mapLayers().values():
            # providerType() == "ogr" schliesst unsere eigenen, bei einem
            # vorherigen Vergleich erzeugten Memory-Layer (Zustand A/B,
            # Unterschiede, Attributaenderungen) aus - die haben zwar auch
            # ein 'gueltig_von'-Feld, aber keinen echten GeoPackage-Pfad.
            if (isinstance(layer, QgsVectorLayer)
                    and layer.providerType() == "ogr"
                    and layer.fields().indexOf("gueltig_von") >= 0):
                self.layer_combo.addItem(layer.name(), layer)
                self.layer_combo.setItemData(self.layer_combo.count() - 1, layer.name(), NAME_ROLLE)
        self._dropdown_texte_aktualisieren()
        # Bisherige Auswahl beibehalten (der Reiterwechsel fuellt die Liste
        # jedes Mal neu - ohne das sprang die Auswahl immer auf den ersten Eintrag)
        if vorher is not None:
            idx = self.layer_combo.findData(vorher, NAME_ROLLE)
            if idx >= 0:
                self.layer_combo.setCurrentIndex(idx)
        self.layer_combo.blockSignals(False)
        # Kein Popup hier (mehr): dieser Tab wird beim Oeffnen des
        # Hauptdialogs immer sofort erzeugt, auch wenn der Nutzer nur den
        # Import-Reiter sehen will - ein Meldungsfenster wuerde da unpassend
        # aufpoppen. Ein leeres Dropdown genuegt als stiller Hinweis; beim
        # Klick auf "Vergleichen" kommt ohnehin eine Meldung, falls kein
        # Layer gewaehlt ist.
        self.lieferungen_aktualisieren()
        self._attr_button_aktualisieren()

    def aktueller_layer(self):
        return self.layer_combo.currentData()

    def lieferungen_aktualisieren(self):
        self.lieferung_a_combo.clear()
        self.lieferung_b_combo.clear()
        layer = self.aktueller_layer()
        if layer is None:
            return
        gpkg_path = _gpkg_path_from_layer(layer)
        lieferungen = _lieferungen_lesen(gpkg_path)
        if not lieferungen:
            QMessageBox.warning(
                self, "Keine Lieferungen gefunden",
                "In diesem GeoPackage wurde keine 'nas_lieferungen'-Tabelle gefunden."
            )
            return
        for dateiname, abgabe_ende in lieferungen:
            label = f"{abgabe_ende}  ({dateiname})"
            self.lieferung_a_combo.addItem(label, abgabe_ende)
            self.lieferung_b_combo.addItem(label, abgabe_ende)
        self.lieferung_a_combo.setCurrentIndex(0)
        self.lieferung_b_combo.setCurrentIndex(self.lieferung_b_combo.count() - 1)

    def vergleichen(self):
        layer = self.aktueller_layer()
        if layer is None:
            QMessageBox.warning(self, "Fehler", "Kein Layer ausgewaehlt.")
            return
        iso_a = self.lieferung_a_combo.currentData()
        iso_b = self.lieferung_b_combo.currentData()
        if iso_a == iso_b:
            QMessageBox.warning(self, "Fehler", "Zeitpunkt A und B sind identisch.")
            return
        if iso_a > iso_b:
            iso_a, iso_b = iso_b, iso_a

        self._fuehre_vergleich_aus(layer, iso_a, iso_b)

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
        # Attributaenderungen-Fenster, Blinkvergleich und Wertklassenflaechen-
        # Ermittlung greifen darauf zu - ein spaeterer Vergleich einer anderen
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
