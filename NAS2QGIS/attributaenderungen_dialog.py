"""
Zeigt Attributaenderungen aus einem Vergleich gruppiert nach Objekt an
(ein Objekt = ein aufklappbarer Eintrag mit allen seinen geaenderten
Feldern darunter), statt einer flachen Tabelle mit einer Zeile je
geaendertem Feld (dort stand jedes mehrfach geaenderte Objekt mehrfach
in der Liste).
"""

import csv

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.core import QgsCoordinateTransform, QgsGeometry, QgsProject


class AttributaenderungenDialog(QDialog):
    """aenderungen: Liste von Dicts:
       {"objektart": str, "oid": str, "geometrie": QgsGeometry oder None,
        "felder": [(feldname, alter_wert, neuer_wert), ...]}
    crs: QgsCoordinateReferenceSystem der uebergebenen Geometrien (i.d.R. die
    CRS des Quell-Layers aus dem GeoPackage) - fuer Zoom/Aufleuchten wird bei
    Bedarf ins aktuelle Projekt-CRS umgerechnet."""

    def __init__(self, iface, aenderungen, crs, blink_controller, blink_paar,
                 objektart, zeitraum, parent=None):
        """blink_paar: (Zustand-A-Layer, Zustand-B-Layer) des Vergleichs, zu dem
        dieses Fenster gehoert - der Blinkvergleich laeuft immer auf genau
        diesem Paar. zeitraum: (iso_a, iso_b)."""
        super().__init__(parent)
        self.iface = iface
        self._crs = crs
        self.blink_controller = blink_controller
        self._blink_paar = blink_paar
        self.setWindowTitle(
            f"Attributänderungen – {objektart} ({zeitraum[0][:10]} → {zeitraum[1][:10]}), "
            f"{len(aenderungen)} Objekt(e)"
        )
        self.setMinimumSize(720, 560)

        layout = QVBoxLayout(self)

        self.baum = QTreeWidget()
        self.baum.setColumnCount(3)
        self.baum.setHeaderLabels(["Objekt / Feld", "Alter Wert", "Neuer Wert"])
        self.baum.itemDoubleClicked.connect(self._bei_doppelklick_zoomen)
        layout.addWidget(self.baum)

        for eintrag in aenderungen:
            anzahl = len(eintrag["felder"])
            titel = f"{eintrag['objektart']}  –  oid={eintrag['oid']}  ({anzahl} Änderung{'en' if anzahl != 1 else ''})"
            top = QTreeWidgetItem([titel, "", ""])
            top.setData(0, Qt.UserRole, eintrag)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for feldname, alt, neu in eintrag["felder"]:
                top.addChild(QTreeWidgetItem([feldname, alt, neu]))
            self.baum.addTopLevelItem(top)

        if not aenderungen:
            hinweis = QTreeWidgetItem(["Keine Attributänderungen zwischen den gewählten Zeitpunkten.", "", ""])
            hinweis.setFlags(Qt.NoItemFlags)
            self.baum.addTopLevelItem(hinweis)

        self.baum.expandAll()
        for spalte in range(3):
            self.baum.resizeColumnToContents(spalte)

        button_row = QHBoxLayout()
        zoom_button = QPushButton("Auf ausgewähltes Objekt zoomen")
        zoom_button.clicked.connect(self._zoomen)
        blink_button = QPushButton("Objekt aufleuchten lassen")
        blink_button.clicked.connect(self._aufleuchten_lassen)
        oid_button = QPushButton("OID kopieren")
        oid_button.clicked.connect(self._oid_kopieren)
        export_button = QPushButton("Exportieren...")
        export_button.clicked.connect(self._exportieren)
        schliessen_button = QPushButton("Schließen")
        schliessen_button.clicked.connect(self.close)
        button_row.addWidget(zoom_button)
        button_row.addWidget(blink_button)
        button_row.addWidget(oid_button)
        button_row.addWidget(export_button)
        button_row.addWidget(schliessen_button)
        layout.addLayout(button_row)

        blinkvergleich_row = QHBoxLayout()
        self.blinkvergleich_button = QPushButton("Blinkvergleich starten")
        self.blinkvergleich_intervall = QSpinBox()
        self.blinkvergleich_intervall.setRange(200, 5000)
        self.blinkvergleich_intervall.setSingleStep(100)
        self.blinkvergleich_intervall.setValue(800)
        self.blinkvergleich_intervall.setSuffix(" ms")
        blinkvergleich_row.addWidget(self.blinkvergleich_button)
        blinkvergleich_row.addWidget(QLabel("Takt:"))
        blinkvergleich_row.addWidget(self.blinkvergleich_intervall)
        layout.addLayout(blinkvergleich_row)

        self.blinkvergleich_button.clicked.connect(self._blinkvergleich_starten_stoppen)
        self.blinkvergleich_intervall.valueChanged.connect(self._blinkvergleich_intervall_geaendert)
        self._blinkvergleich_ui_aktualisieren()

    def _oberstes_element(self, item):
        while item.parent() is not None:
            item = item.parent()
        return item

    def _ausgewaehltes_objekt(self):
        auswahl = self.baum.selectedItems()
        if not auswahl:
            QMessageBox.information(self, "Kein Objekt ausgewählt", "Bitte zuerst eine Zeile in der Liste auswählen.")
            return None
        top = self._oberstes_element(auswahl[0])
        return top.data(0, Qt.UserRole)

    def _geom_in_projekt_crs(self, geom):
        """Kopie der Geometrie, ins aktuelle Projekt-/Karten-CRS umgerechnet
        (nur falls sich das Quell-CRS davon unterscheidet)."""
        ziel_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if not self._crs.isValid() or self._crs == ziel_crs:
            return geom
        transform = QgsCoordinateTransform(self._crs, ziel_crs, QgsProject.instance())
        kopie = QgsGeometry(geom)
        kopie.transform(transform)
        return kopie

    def _geometrie_pruefen(self, eintrag):
        geom = eintrag["geometrie"] if eintrag else None
        if geom is None or geom.isEmpty():
            QMessageBox.information(self, "Keine Geometrie", "Für dieses Objekt ist keine Geometrie hinterlegt.")
            return None
        return geom

    def _zoomen(self):
        eintrag = self._ausgewaehltes_objekt()
        if eintrag is None:
            return
        geom = self._geometrie_pruefen(eintrag)
        if geom is None:
            return
        geom = self._geom_in_projekt_crs(geom)
        canvas = self.iface.mapCanvas()
        canvas.setExtent(geom.boundingBox())
        canvas.zoomByFactor(1.3)  # etwas herauszoomen, damit die Kontur nicht exakt am Rand klebt
        canvas.refresh()

    def _aufleuchten_lassen(self):
        eintrag = self._ausgewaehltes_objekt()
        if eintrag is None:
            return
        geom = self._geometrie_pruefen(eintrag)
        if geom is None:
            return
        self.iface.mapCanvas().flashGeometries([geom], self._crs)

    def _oid_kopieren(self):
        eintrag = self._ausgewaehltes_objekt()
        if eintrag is None:
            return
        QApplication.clipboard().setText(eintrag["oid"])
        self.iface.messageBar().pushInfo("NAS2QGIS", f"OID kopiert: {eintrag['oid']}")

    def _exportieren(self):
        pfad, _ = QFileDialog.getSaveFileName(
            self, "Als CSV exportieren", "attributaenderungen.csv", "CSV-Datei (*.csv)"
        )
        if not pfad:
            return
        if not pfad.lower().endswith(".csv"):
            pfad += ".csv"
        try:
            with open(pfad, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Objektart", "OID", "Feld", "Alter_Wert", "Neuer_Wert"])
                for i in range(self.baum.topLevelItemCount()):
                    top = self.baum.topLevelItem(i)
                    eintrag = top.data(0, Qt.UserRole)
                    for j in range(top.childCount()):
                        kind = top.child(j)
                        writer.writerow([
                            eintrag["objektart"], eintrag["oid"], kind.text(0), kind.text(1), kind.text(2)
                        ])
        except OSError as exc:
            QMessageBox.critical(self, "Fehler beim Export", str(exc))
            return
        self.iface.messageBar().pushSuccess("NAS2QGIS", f"Exportiert nach {pfad}")

    def _bei_doppelklick_zoomen(self, item, spalte):
        self.baum.setCurrentItem(item)
        self._zoomen()

    # -- Blinkvergleich: delegiert an den BlinkController der Plugin-Instanz,
    # damit er auch nach dem Schliessen dieses Fensters weiterlaeuft ---------

    def _paar_gueltig(self):
        a, b = self._blink_paar
        for layer in (a, b):
            if layer is None or sip.isdeleted(layer):
                return False
            if QgsProject.instance().mapLayer(layer.id()) is None:
                return False
        return True

    def _ist_aktives_paar(self):
        """True nur, wenn der Blinkvergleich gerade auf DEM Layerpaar dieses
        Fensters laeuft (nicht auf dem einer anderen Objektart)."""
        if not self.blink_controller.ist_aktiv() or not self._paar_gueltig():
            return False
        a, b = self._blink_paar
        return self.blink_controller.paar_ids() == (a.id(), b.id())

    def _blinkvergleich_ui_aktualisieren(self):
        self.blinkvergleich_button.setEnabled(self._paar_gueltig())
        if self._ist_aktives_paar():
            self.blinkvergleich_button.setText("Blinkvergleich stoppen")
        else:
            self.blinkvergleich_button.setText("Blinkvergleich starten")

    def _blinkvergleich_starten_stoppen(self):
        if self._ist_aktives_paar():
            self.blink_controller.stoppen()
        else:
            if not self._paar_gueltig():
                QMessageBox.warning(
                    self, "Fehler",
                    "Die Vergleichs-Layer sind nicht mehr vorhanden. Bitte den Vergleich erneut ausführen."
                )
                return
            a, b = self._blink_paar
            if self.blink_controller.paar_ids() != (a.id(), b.id()):
                # Der Controller haelt noch das Paar einer anderen Objektart
                # (oder keins) - auf das Paar dieses Fensters umstellen.
                self.blink_controller.layer_setzen(a, b)
            self.blink_controller.starten(self.blinkvergleich_intervall.value())
        self._blinkvergleich_ui_aktualisieren()

    def _blinkvergleich_intervall_geaendert(self, wert):
        if self._ist_aktives_paar():
            self.blink_controller.intervall_setzen(wert)
