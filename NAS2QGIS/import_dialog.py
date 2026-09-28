import os
import sqlite3

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsVectorLayer, QgsProject

from .parser import parse_delivery_metadata, verfahrensnummer_aus_auftragsnummer
from .writer import import_delivery, finalize_geopackage
from .styling import style_layer
from .beteiligung import berechne_beteiligtenstatus

# Objektarten, die im Auswahldialog standardmaessig angehakt sind.
# Einfach anpassen - Tabellennamen entsprechen den Objektarten aus der NAS-Datei
# (z.B. AX_Flurstueck, AX_Gebaeude, AX_Grenzpunkt, ...).
STANDARD_LAYER_AUSWAHL = {
    "AX_Flurstueck",
}


class LayerAuswahlDialog(QDialog):
    """Checkbox-Liste, um vor dem Laden auszuwaehlen, welche Objektart-Tabellen
    tatsaechlich als Layer ins Projekt sollen.

    vorausgewaehlt: Menge/Liste von Tabellennamen, die beim Oeffnen bereits
    angehakt sein sollen. Ohne Angabe (None) werden alle angehakt."""

    def __init__(self, tabellen, vorausgewaehlt=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Layer auswaehlen")
        self.setMinimumSize(340, 440)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Welche Objektarten sollen geladen werden?"))

        self.liste = QListWidget()
        for tabelle in tabellen:
            item = QListWidgetItem(tabelle)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            angehakt = True if vorausgewaehlt is None else tabelle in vorausgewaehlt
            item.setCheckState(Qt.Checked if angehakt else Qt.Unchecked)
            self.liste.addItem(item)
        layout.addWidget(self.liste)

        auswahl_row = QHBoxLayout()
        alle_button = QPushButton("Alle")
        alle_button.clicked.connect(lambda: self._alle_setzen(Qt.Checked))
        keine_button = QPushButton("Keine")
        keine_button.clicked.connect(lambda: self._alle_setzen(Qt.Unchecked))
        auswahl_row.addWidget(alle_button)
        auswahl_row.addWidget(keine_button)
        layout.addLayout(auswahl_row)

        button_row = QHBoxLayout()
        ok_button = QPushButton("Laden")
        # Leicht gruen, damit der Button sich von Alle/Keine/Abbrechen abhebt
        ok_button.setStyleSheet(
            "QPushButton { background-color: #c8e6c9; border: 1px solid #81c784; "
            "border-radius: 3px; padding: 4px 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #a5d6a7; }"
            "QPushButton:pressed { background-color: #81c784; }"
        )
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("Abbrechen")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(ok_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _alle_setzen(self, state):
        for i in range(self.liste.count()):
            self.liste.item(i).setCheckState(state)

    def ausgewaehlte_tabellen(self):
        return [
            self.liste.item(i).text()
            for i in range(self.liste.count())
            if self.liste.item(i).checkState() == Qt.Checked
        ]


class ImportTab(QWidget):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.gpkg_pfad = plugin.letztes_gpkg

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("1. Ziel-GeoPackage:"))
        gpkg_row = QHBoxLayout()
        self.gpkg_label = QLabel(self.gpkg_pfad if self.gpkg_pfad else "(noch nicht gewaehlt)")
        gpkg_button = QPushButton("Auswaehlen...")
        gpkg_button.clicked.connect(self.gpkg_waehlen)
        gpkg_row.addWidget(self.gpkg_label, stretch=1)
        gpkg_row.addWidget(gpkg_button)
        layout.addLayout(gpkg_row)

        layout.addWidget(QLabel("Bereits im GeoPackage enthalten:"))
        self.bereits_tabelle = QTableWidget()
        self.bereits_tabelle.setColumnCount(3)
        self.bereits_tabelle.setHorizontalHeaderLabels(["Verfahrensnummer", "Datum", "Dateiname"])
        self.bereits_tabelle.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bereits_tabelle.setSelectionBehavior(QTableWidget.SelectRows)
        self.bereits_tabelle.horizontalHeader().setStretchLastSection(True)
        self.bereits_tabelle.setMaximumHeight(130)
        layout.addWidget(self.bereits_tabelle)
        self._bereits_eingelesen_aktualisieren()

        layout.addWidget(QLabel("NAS-Dateien (Erstabgabe + Differenzabgaben, Reihenfolge egal):"))
        self.datei_liste = QListWidget()
        layout.addWidget(self.datei_liste)

        datei_row = QHBoxLayout()
        add_button = QPushButton("2. Dateien hinzufuegen...")
        add_button.clicked.connect(self.dateien_hinzufuegen)
        clear_button = QPushButton("Liste leeren")
        clear_button.clicked.connect(self.datei_liste.clear)
        datei_row.addWidget(add_button)
        datei_row.addWidget(clear_button)
        layout.addLayout(datei_row)

        self.start_button = QPushButton("3. Importieren")
        self.start_button.clicked.connect(self.import_starten)
        layout.addWidget(self.start_button)

        laden_button = QPushButton("4. GeoPackage in QGIS laden")
        laden_button.clicked.connect(self.gpkg_laden)
        layout.addWidget(laden_button)

        layout.addWidget(QLabel("Protokoll:"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)

    def gpkg_waehlen(self):
        pfad, _ = QFileDialog.getSaveFileName(
            self, "Ziel-GeoPackage waehlen (neu oder bestehend)", "", "GeoPackage (*.gpkg)"
        )
        if pfad:
            if not pfad.lower().endswith(".gpkg"):
                pfad += ".gpkg"
            self.gpkg_pfad = pfad
            self.gpkg_label.setText(pfad)
            self.plugin.letztes_gpkg = pfad
            self._bereits_eingelesen_aktualisieren()

    def _bereits_eingelesen_aktualisieren(self):
        """Zeigt an, welche Lieferungen bereits im gewaehlten GeoPackage
        stecken (aus der nas_lieferungen-Tabelle) - inkl. Verfahrensnummer,
        aus der auftragsnummer (Rueckfall: antragsnummer) abgeleitet."""
        self.bereits_tabelle.setRowCount(0)
        if not self.gpkg_pfad:
            return
        if not os.path.exists(self.gpkg_pfad):
            self._hinweis_anzeigen(
                "Neues GeoPackage - Datei existiert noch nicht, wird beim Importieren angelegt."
            )
            return
        try:
            conn = sqlite3.connect(self.gpkg_pfad)
            zeilen = conn.execute(
                "SELECT dateiname, antragsnummer, auftragsnummer, abgabeintervallEnde "
                "FROM nas_lieferungen ORDER BY abgabeintervallEnde"
            ).fetchall()
            conn.close()
        except sqlite3.OperationalError:
            self._hinweis_anzeigen(
                "Keine Protokoll-Tabelle gefunden - vermutlich mit einer älteren Plugin-Version erstellt."
            )
            return

        self.bereits_tabelle.setRowCount(len(zeilen))
        for zeile, (dateiname, antragsnummer, auftragsnummer, abgabe_ende) in enumerate(zeilen):
            verfahrensnummer = (
                verfahrensnummer_aus_auftragsnummer(auftragsnummer)
                or verfahrensnummer_aus_auftragsnummer(antragsnummer)
                or "?"
            )
            for spalte, wert in enumerate([verfahrensnummer, abgabe_ende or "", dateiname]):
                self.bereits_tabelle.setItem(zeile, spalte, QTableWidgetItem(wert))
        self.bereits_tabelle.resizeColumnsToContents()

    def _hinweis_anzeigen(self, text):
        self.bereits_tabelle.setRowCount(1)
        self.bereits_tabelle.setItem(0, 0, QTableWidgetItem(text))
        self.bereits_tabelle.setSpan(0, 0, 1, 3)

    def dateien_hinzufuegen(self):
        pfade, _ = QFileDialog.getOpenFileNames(self, "NAS-XML-Dateien waehlen", "", "NAS-Dateien (*.xml)")
        for p in pfade:
            vorhandene = [self.datei_liste.item(i).text() for i in range(self.datei_liste.count())]
            if p not in vorhandene:
                self.datei_liste.addItem(p)

    def _log(self, text):
        self.log.appendPlainText(text)
        self.log.repaint()

    def import_starten(self):
        if not self.gpkg_pfad:
            QMessageBox.warning(self, "Fehler", "Bitte zuerst ein Ziel-GeoPackage waehlen.")
            return
        pfade = [self.datei_liste.item(i).text() for i in range(self.datei_liste.count())]
        if not pfade:
            QMessageBox.warning(self, "Fehler", "Bitte mindestens eine NAS-Datei hinzufuegen.")
            return

        self.log.clear()
        self._log(f"{len(pfade)} Datei(en), ermittle chronologische Reihenfolge...")

        mit_datum = []
        for p in pfade:
            try:
                meta = parse_delivery_metadata(p)
            except Exception as exc:
                self._log(f"FEHLER beim Lesen von {os.path.basename(p)}: {exc}")
                return
            ende = meta.get("abgabeintervallEnde") or "9999-99-99"
            mit_datum.append((ende, p))
        mit_datum.sort(key=lambda t: t[0])

        self.start_button.setEnabled(False)
        try:
            for ende, pfad in mit_datum:
                self._log(f"\n=== {os.path.basename(pfad)} (abgabeintervallEnde={ende}) ===")
                try:
                    counts, meta = import_delivery(self.gpkg_pfad, pfad)
                except Exception as exc:
                    self._log(f"FEHLER: {exc}")
                    QMessageBox.critical(self, "Fehler beim Import", str(exc))
                    return
                self._log(f"  Insert: {counts['insert']}, Replace: {counts['replace']}, Delete: {counts['delete']}")

            self._log("\n=== Finalisiere GeoPackage ===")
            finalize_geopackage(self.gpkg_pfad)

            self._log("\n=== Ermittle Beteiligtenstatus (AX_Flurstueck <-> BRB) ===")
            berechne_beteiligtenstatus(self.gpkg_pfad, log=self._log)

            self._log("Fertig.")
            self._bereits_eingelesen_aktualisieren()
            QMessageBox.information(self, "Import abgeschlossen", "Alle Dateien wurden eingespielt.")
        finally:
            self.start_button.setEnabled(True)

    def gpkg_laden(self):
        if not self.gpkg_pfad or not os.path.exists(self.gpkg_pfad):
            QMessageBox.warning(self, "Fehler", "Kein gueltiges GeoPackage gewaehlt bzw. noch nicht erzeugt.")
            return
        import sqlite3
        conn = sqlite3.connect(self.gpkg_pfad)
        tabellen = [r[0] for r in conn.execute(
            "SELECT table_name FROM gpkg_contents WHERE data_type='features' ORDER BY table_name"
        )]
        conn.close()

        if not tabellen:
            QMessageBox.information(self, "Keine Layer", "Keine Objektart-Tabellen mit Geometrie gefunden.")
            return

        auswahl_dialog = LayerAuswahlDialog(tabellen, vorausgewaehlt=STANDARD_LAYER_AUSWAHL, parent=self)
        if auswahl_dialog.exec_() != QDialog.Accepted:
            return
        ausgewaehlt = auswahl_dialog.ausgewaehlte_tabellen()

        geladen = 0
        erste_gueltige_crs = None
        for index, tabelle in enumerate(ausgewaehlt):
            layer = QgsVectorLayer(f"{self.gpkg_pfad}|layername={tabelle}", tabelle, "ogr")
            if layer.isValid():
                style_layer(layer, tabelle, index)
                QgsProject.instance().addMapLayer(layer)
                if erste_gueltige_crs is None and layer.crs().isValid():
                    erste_gueltige_crs = layer.crs()
                geladen += 1
        self._log(f"\n{geladen} von {len(tabellen)} verfuegbaren Layern ins Projekt geladen.")

        if erste_gueltige_crs is not None:
            projekt_crs = QgsProject.instance().crs()
            if projekt_crs != erste_gueltige_crs:
                QgsProject.instance().setCrs(erste_gueltige_crs)
                self._log(
                    f"Projekt-CRS auf {erste_gueltige_crs.authid()} gesetzt "
                    f"(vorher: {projekt_crs.authid() or 'nicht gesetzt'})."
                )
