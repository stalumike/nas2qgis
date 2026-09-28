"""
Verschneidet den Differenzlayer eines AX_Flurstueck-Vergleichs mit den
Wertklassenflaechen (aus dem separaten lefistogeopackage-Plugin) und listet
gruppiert je geaendertem Flurstueck auf, welche Wertklassenflaechen davon
beruehrt werden - das ist die fuer die Homogenisierung relevante Richtung
("welche Wertklassenflaechen muss ich mir fuer dieses Flurstueck anschauen"),
nicht umgekehrt.

Voraussetzungen, die vor dem Ausfuehren geprueft werden:
  1. Es liegt ein Vergleichsergebnis fuer AX_Flurstueck vor (Differenzlayer
     der Plugin-Instanz, siehe vergleich_dialog.py -> plugin.vergleiche).
  2. Das GeoPackage mit den Wertklassenflaechen (AB-Wertklassenflaechen.gpkg,
     vom Nutzer ueber das lefistogeopackage-Plugin erzeugt - diese Logik wird
     hier bewusst NICHT nachgebaut) wird automatisch im selben Ordner wie das
     NAS-GeoPackage des Vergleichs gesucht und intern geoeffnet, ohne den Layer
     ins Projekt zu laden. Ist bereits ein passender Layer im Projekt
     geladen, wird dieser bevorzugt. Nur wenn nichts gefunden wird, fragt
     das Plugin nach dem Speicherort.

Nur Differenzobjekte, die tatsaechlich eine geometrische Aenderung darstellen
(neu, entfernt, geometrisch geaendert) werden verschnitten - reine
Attributaenderungen ohne Geometrieaenderung sind fuer die Wertermittlung
irrelevant.
"""

import csv
import os

from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsSpatialIndex,
    QgsVectorLayer,
)

WERTKLASSEN_TABELLE = "AB-Wertklassenflaechen"
WERTKLASSEN_DATEINAME = "AB-Wertklassenflaechen.gpkg"
WERTKLASSEN_OID_FELD = "uuid"
WERTKLASSEN_WEKL_FELD = "wekl"
WERTKLASSEN_NUNK_FELD = "nunk"

# Vergleichsstatus, die eine echte geometrische Aenderung darstellen und
# daher fuer die Wertermittlung relevant sind - reine Attributaenderungen
# (keine Geometrieaenderung) werden nicht verschnitten.
GEOMETRISCH_RELEVANTE_STATI = {"neu", "entfernt", "geometrie_geaendert", "geometrie_und_attribute_geaendert"}


def _diff_layer_gueltig(vergleich):
    if vergleich is None:
        return False
    diff_layer = vergleich.get("diff_layer")
    if diff_layer is None or sip.isdeleted(diff_layer):
        return False
    return QgsProject.instance().mapLayer(diff_layer.id()) is not None


def _finde_wertklassen_layer():
    """Sucht einen geladenen Layer, dessen GeoPackage-Quelltabelle
    WERTKLASSEN_TABELLE heisst - unabhaengig davon, wie der Layer im
    Projekt umbenannt wurde."""
    for layer in QgsProject.instance().mapLayers().values():
        if not isinstance(layer, QgsVectorLayer):
            continue
        if f"layername={WERTKLASSEN_TABELLE}" in layer.source():
            return layer
    return None


def _oeffne_wertklassen_gpkg(pfad):
    """Oeffnet die Wertklassenflaechen-Tabelle aus dem GeoPackage als
    Layer, der bewusst NICHT ins Projekt geladen wird (der Nutzer soll ihn
    nicht selbst reinziehen muessen und auch nicht im Layerbaum haben).
    None, falls die Datei/Tabelle nicht lesbar ist."""
    layer = QgsVectorLayer(f"{pfad}|layername={WERTKLASSEN_TABELLE}", "wertklassen", "ogr")
    return layer if layer.isValid() else None


def _beschaffe_wertklassen_layer(iface, vergleich):
    """Ermittelt den Wertklassenflaechen-Layer: 1. schon im Projekt geladen,
    2. AB-Wertklassenflaechen.gpkg im Ordner des NAS-GeoPackages des
    Vergleichs, 3. Nachfrage beim Nutzer. Rueckgabe None = abgebrochen/nicht
    verfuegbar (Meldung wurde bereits gezeigt)."""
    layer = _finde_wertklassen_layer()
    if layer is not None:
        return layer

    nas_gpkg = vergleich.get("gpkg_pfad")
    kandidat = None
    if nas_gpkg:
        kandidat = os.path.join(os.path.dirname(nas_gpkg), WERTKLASSEN_DATEINAME)
        if os.path.exists(kandidat):
            layer = _oeffne_wertklassen_gpkg(kandidat)
            if layer is not None:
                return layer
            QMessageBox.warning(
                iface.mainWindow(), "Wertklassenflächen nicht lesbar",
                f"Die Datei\n{kandidat}\nwurde gefunden, enthält aber keine lesbare "
                f"Tabelle '{WERTKLASSEN_TABELLE}'."
            )
            return None

    ort = os.path.dirname(kandidat) if kandidat else "dem Ordner des NAS-GeoPackages"
    antwort = QMessageBox.question(
        iface.mainWindow(), "Wertklassenflächen nicht gefunden",
        f"Im Ordner\n{ort}\nliegt keine Datei '{WERTKLASSEN_DATEINAME}'.\n\n"
        "Bitte die Wertklassenflächen zuvor mit dem lefistogeopackage-Plugin "
        "erzeugen und dort ablegen.\n\nJetzt manuell eine Datei auswählen?"
    )
    if antwort != QMessageBox.Yes:
        return None
    pfad, _ = QFileDialog.getOpenFileName(
        iface.mainWindow(), "Wertklassenflächen-GeoPackage wählen",
        os.path.dirname(kandidat) if kandidat else "", "GeoPackage (*.gpkg)"
    )
    if not pfad:
        return None
    layer = _oeffne_wertklassen_gpkg(pfad)
    if layer is None:
        QMessageBox.warning(
            iface.mainWindow(), "Wertklassenflächen nicht lesbar",
            f"Die gewählte Datei enthält keine lesbare Tabelle '{WERTKLASSEN_TABELLE}'."
        )
    return layer


def oeffne_wertklassen_dialog(iface, plugin):
    """Prueft beide Voraussetzungen und oeffnet bei Erfolg das
    Ergebnis-Fenster - sonst eine erklaerende Meldung, was fehlt."""
    # Der Vergleich je Objektart wird im Plugin getrennt gehalten - es zaehlt
    # der letzte AX_Flurstueck-Vergleich, egal ob danach andere Objektarten
    # verglichen wurden.
    vergleich = plugin.vergleiche.get("AX_Flurstueck")
    if not _diff_layer_gueltig(vergleich):
        QMessageBox.information(
            iface.mainWindow(), "Kein Flurstücksvergleich vorhanden",
            "Es liegt kein aktueller Vergleich für AX_Flurstueck vor.\n\n"
            "Bitte zuerst über den Reiter 'Vergleich' einen Vergleich "
            "für die Objektart AX_Flurstueck durchführen."
        )
        return

    wertklassen_layer = _beschaffe_wertklassen_layer(iface, vergleich)
    if wertklassen_layer is None:
        return

    ergebnisse = _ermittle_betroffene_wertklassen(
        vergleich["diff_layer"],
        vergleich.get("zustand_a_layer"),
        vergleich.get("zustand_b_layer"),
        vergleich["crs"],
        wertklassen_layer,
    )
    if not ergebnisse:
        QMessageBox.information(
            iface.mainWindow(), "Keine betroffenen Wertklassenflächen",
            "Keine Wertklassenfläche überschneidet sich mit einer geometrischen "
            "Änderung aus dem letzten Flurstücksvergleich."
        )
        return

    # Elternfenster = QGIS-Hauptfenster (wie beim Attributaenderungen-Fenster):
    # ohne Elternfenster faellt das Fenster hinter die Karte, sobald der
    # Hauptdialog geschlossen wird, und wird nicht sicher von Qt verwaltet.
    dlg = WertklassenDialog(iface, ergebnisse, vergleich["crs"], iface.mainWindow())
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


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


def _ermittle_betroffene_wertklassen(diff_layer, zustand_a_layer, zustand_b_layer, diff_crs, wertklassen_layer):
    """Liefert je geaendertem Flurstueck die davon beruehrten
    Wertklassenflaechen. Rueckgabe: Liste von Dicts
    {oid, geometrie (im diff_crs), wertklassenflaechen: [{uuid, wekl, geometrie}, ...]}.

    'geometrie' beim Flurstueck ist bewusst NICHT die (oft sehr duenne)
    Differenzflaeche, sondern - falls auffindbar - die volle aktuelle
    Flurstuecksflaeche (Zustand B, Rueckfall Zustand A) - fuers Zoomen/
    Aufleuchten ist eine ganze Flaeche sichtbar sinnvoll, ein hauchduenner
    Streifen praktisch nicht. Fuer die Verschneidung mit den
    Wertklassenflaechen wird weiterhin die tatsaechliche Aenderungsflaeche
    (aus dem Differenzlayer) verwendet - das ist fachlich der richtige
    Massstab dafuer, welche Wertklassenflaechen tatsaechlich betroffen sind.
    Alle Geometrien werden konsistent im diff_crs zurueckgegeben, damit der
    Dialog nur mit einem einzigen CRS umgehen muss.
    """
    ziel_crs = wertklassen_layer.crs()
    hin_transform = None
    rueck_transform = None
    if diff_crs.isValid() and diff_crs != ziel_crs:
        hin_transform = QgsCoordinateTransform(diff_crs, ziel_crs, QgsProject.instance())
        rueck_transform = QgsCoordinateTransform(ziel_crs, diff_crs, QgsProject.instance())

    # Wertklassenflaechen einmalig einlesen + in einen Spatial Index packen,
    # damit wir nicht fuer jedes geaenderte Flurstueck alle 2000+ Flaechen
    # einzeln pruefen muessen.
    oid_idx = wertklassen_layer.fields().indexOf(WERTKLASSEN_OID_FELD)
    wekl_idx = wertklassen_layer.fields().indexOf(WERTKLASSEN_WEKL_FELD)
    nunk_idx = wertklassen_layer.fields().indexOf(WERTKLASSEN_NUNK_FELD)
    wk_geom_cache = {}
    wk_attr_cache = {}
    index = QgsSpatialIndex()
    for wk_feat in wertklassen_layer.getFeatures():
        geom = _sichere_geom(wk_feat.geometry())
        if geom is None:
            continue
        wk_geom_cache[wk_feat.id()] = geom
        wk_attr_cache[wk_feat.id()] = {
            "uuid": wk_feat[oid_idx] if oid_idx >= 0 else str(wk_feat.id()),
            "wekl": wk_feat[wekl_idx] if wekl_idx >= 0 else None,
            "nunk": wk_feat[nunk_idx] if nunk_idx >= 0 else None,
        }
        indexierbar = QgsFeature(wk_feat.id())
        indexierbar.setGeometry(geom)
        index.addFeature(indexierbar)

    status_feld_idx = diff_layer.fields().indexOf("vergleichsstatus")
    oid_feld_idx = diff_layer.fields().indexOf("oid")

    # Volle Flurstuecksgeometrie je OID nachschlagbar machen (Zustand B
    # bevorzugt = aktueller/neuer Stand, Zustand A als Rueckfall fuer
    # entfernte Flurstuecke, die in B gar nicht mehr existieren).
    volle_geom_je_oid = {}
    for quell_layer in (zustand_a_layer, zustand_b_layer):
        if quell_layer is None or sip.isdeleted(quell_layer):
            continue
        oid_idx_quelle = quell_layer.fields().indexOf("oid")
        if oid_idx_quelle < 0:
            continue
        for f in quell_layer.getFeatures():
            geom = _sichere_geom(f.geometry())
            if geom is not None:
                volle_geom_je_oid[f["oid"]] = geom  # Zustand B ueberschreibt A, da spaeter in der Schleife

    ergebnisse = []
    for feat in diff_layer.getFeatures():
        if status_feld_idx >= 0 and feat["vergleichsstatus"] not in GEOMETRISCH_RELEVANTE_STATI:
            continue
        original_geom = _sichere_geom(feat.geometry())
        if original_geom is None:
            continue

        oid = feat["oid"] if oid_feld_idx >= 0 else str(feat.id())
        anzeige_geom_flurstueck = volle_geom_je_oid.get(oid, original_geom)

        test_geom = original_geom
        if hin_transform is not None:
            test_geom = QgsGeometry(original_geom)
            test_geom.transform(hin_transform)

        betroffen = []
        for wk_fid in index.intersects(test_geom.boundingBox()):
            wk_geom = wk_geom_cache.get(wk_fid)
            if wk_geom is None or not wk_geom.intersects(test_geom):
                continue
            anzeige_geom = wk_geom
            if rueck_transform is not None:
                anzeige_geom = QgsGeometry(wk_geom)
                anzeige_geom.transform(rueck_transform)
            eintrag = dict(wk_attr_cache[wk_fid])
            eintrag["geometrie"] = anzeige_geom
            betroffen.append(eintrag)

        if betroffen:
            ergebnisse.append({
                "oid": oid,
                "geometrie": anzeige_geom_flurstueck,
                "wertklassenflaechen": betroffen,
            })

    return ergebnisse


class WertklassenDialog(QDialog):
    """crs: CRS, in dem ALLE Geometrien in 'ergebnisse' vorliegen (siehe
    _ermittle_betroffene_wertklassen - einheitlich das diff_crs)."""

    def __init__(self, iface, ergebnisse, crs, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._crs = crs
        anzahl_wk_gesamt = len({wk["uuid"] for e in ergebnisse for wk in e["wertklassenflaechen"]})
        self.setWindowTitle(
            f"Betroffene Wertklassenflächen ({len(ergebnisse)} Flurstück(e), "
            f"{anzahl_wk_gesamt} Wertklassenfläche(n))"
        )
        self.setMinimumSize(680, 520)

        layout = QVBoxLayout(self)

        self.baum = QTreeWidget()
        self.baum.setColumnCount(2)
        self.baum.setHeaderLabels(["Flurstück / Wertklassenfläche", "Nutzung Wertklasse"])
        layout.addWidget(self.baum)

        for eintrag in ergebnisse:
            anzahl = len(eintrag["wertklassenflaechen"])
            titel = f"AX_Flurstueck – oid={eintrag['oid']}  ({anzahl} Wertklassenfläche{'n' if anzahl != 1 else ''})"
            top = QTreeWidgetItem([titel, ""])
            top.setData(0, Qt.UserRole, {
                "art": "flurstueck", "oid": eintrag["oid"], "geometrie": eintrag["geometrie"],
            })
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            for wk in eintrag["wertklassenflaechen"]:
                nunk_wekl = " ".join(
                    str(teil) for teil in (wk["nunk"], wk["wekl"]) if teil is not None
                )
                kind = QTreeWidgetItem([str(wk["uuid"]), nunk_wekl])
                kind.setData(0, Qt.UserRole, {
                    "art": "wertklassenflaeche", "uuid": wk["uuid"], "geometrie": wk["geometrie"],
                })
                top.addChild(kind)
            self.baum.addTopLevelItem(top)

        self.baum.expandAll()
        for spalte in range(2):
            self.baum.resizeColumnToContents(spalte)

        button_row = QHBoxLayout()
        zoom_button = QPushButton("Auf Auswahl zoomen")
        zoom_button.clicked.connect(self._zoomen)
        blink_button = QPushButton("Auswahl aufleuchten lassen")
        blink_button.clicked.connect(self._aufleuchten_lassen)
        oid_button = QPushButton("OID/UUID kopieren")
        oid_button.clicked.connect(self._id_kopieren)
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

        self.baum.itemDoubleClicked.connect(self._bei_doppelklick_zoomen)

    def _bei_doppelklick_zoomen(self, item, spalte):
        self.baum.setCurrentItem(item)
        self._zoomen()

    def _ausgewaehltes_element(self):
        auswahl = self.baum.selectedItems()
        if not auswahl:
            QMessageBox.information(self, "Keine Auswahl", "Bitte zuerst eine Zeile in der Liste auswählen.")
            return None
        return auswahl[0].data(0, Qt.UserRole)

    def _geom_in_projekt_crs(self, geom):
        ziel_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if not self._crs.isValid() or self._crs == ziel_crs:
            return geom
        transform = QgsCoordinateTransform(self._crs, ziel_crs, QgsProject.instance())
        kopie = QgsGeometry(geom)
        kopie.transform(transform)
        return kopie

    def _zoomen(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None:
            return
        geom = self._geom_in_projekt_crs(eintrag["geometrie"])
        bbox = geom.boundingBox()
        # Bei sehr schmalen Geometrien (z.B. duenne Differenzflaechen bei
        # geometrisch geaenderten Flurstuecken) reicht ein reiner
        # Skalierungsfaktor nicht aus - deshalb zusaetzlich ein
        # Mindestpuffer in Kartenmasseinheiten, damit man wirklich etwas sieht.
        puffer = max(bbox.width(), bbox.height()) * 0.5
        puffer = max(puffer, 10)  # Mindestens 10 Karteneinheiten (i.d.R. Meter)
        bbox = bbox.buffered(puffer)
        canvas = self.iface.mapCanvas()
        canvas.setExtent(bbox)
        canvas.refresh()

    def _aufleuchten_lassen(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None:
            return
        self.iface.mapCanvas().flashGeometries([eintrag["geometrie"]], self._crs)

    def _id_kopieren(self):
        eintrag = self._ausgewaehltes_element()
        if eintrag is None:
            return
        wert = eintrag["oid"] if eintrag["art"] == "flurstueck" else eintrag["uuid"]
        QApplication.clipboard().setText(str(wert))
        self.iface.messageBar().pushInfo("NAS2QGIS", f"Kopiert: {wert}")

    def _exportieren(self):
        pfad, _ = QFileDialog.getSaveFileName(
            self, "Als CSV exportieren", "wertklassenflaechen.csv", "CSV-Datei (*.csv)"
        )
        if not pfad:
            return
        if not pfad.lower().endswith(".csv"):
            pfad += ".csv"
        try:
            with open(pfad, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["Flurstueck_OID", "Wertklassenflaeche_UUID", "Nutzung_Wertklasse"])
                for i in range(self.baum.topLevelItemCount()):
                    top = self.baum.topLevelItem(i)
                    flurstueck_oid = top.data(0, Qt.UserRole)["oid"]
                    for j in range(top.childCount()):
                        kind = top.child(j)
                        writer.writerow([flurstueck_oid, kind.text(0), kind.text(1)])
        except OSError as exc:
            QMessageBox.critical(self, "Fehler beim Export", str(exc))
            return
        self.iface.messageBar().pushSuccess("NAS2QGIS", f"Exportiert nach {pfad}")
