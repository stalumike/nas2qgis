import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

PLUGIN_DIR = os.path.dirname(__file__)


class Nas2QgisPlugin:
    """Registriert eine einzelne Schaltflaeche/Menuepunkt 'NAS2QGIS
    öffnen...' unter Erweiterungen -> NAS2QGIS, die den Hauptdialog mit den
    Reitern 'Import' und 'Vergleich' oeffnet. Die Wertklassenflaechen-
    Ermittlung ist kein eigener Einstiegspunkt mehr, sondern ein Button im
    Vergleich-Reiter (siehe vergleich_dialog.py)."""

    def __init__(self, iface):
        self.iface = iface
        self.menu = "&NAS2QGIS"
        self.actions = []
        self._hauptdialog = None
        self._attr_dialog = None
        from .blink_controller import BlinkController
        self.blink_controller = BlinkController()
        self.letztes_gpkg = None  # zuletzt gewaehltes Ziel-GeoPackage - ueberlebt Dialog schliessen
        # Ergebnis des jeweils letzten Vergleichs JE OBJEKTART (Schluessel = Layername,
        # z.B. "AX_Flurstueck"): {"diff_layer","zustand_a_layer","zustand_b_layer",
        # "crs","aenderungen","zeitraum"}. Ueberlebt Dialog schliessen; ein neuer
        # Vergleich derselben Objektart ersetzt nur den Eintrag dieser Objektart.
        self.vergleiche = {}
        self._attr_dialog_objektart = None

    def zeige_attributaenderungen(self, objektart, neu_aufbauen=False):
        """Oeffnet das Attributaenderungen-Fenster fuer den letzten Vergleich
        der angegebenen Objektart - unabhaengig davon, ob der Hauptdialog
        noch offen ist. Ist fuer dieselbe Objektart schon ein Fenster offen,
        wird es nur nach vorne geholt (kein zweites Fenster). Ein Fenster
        einer ANDEREN Objektart, oder ein Aufruf nach einem neuen Vergleich
        (neu_aufbauen=True), ersetzt das offene Fenster."""
        from qgis.PyQt import sip
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QMessageBox

        vergleich = self.vergleiche.get(objektart)
        if vergleich is None:
            QMessageBox.information(
                self.iface.mainWindow(), "Keine Daten",
                f"Für '{objektart}' wurde noch kein Vergleich durchgeführt."
            )
            return

        vorhanden = (
            self._attr_dialog is not None
            and not sip.isdeleted(self._attr_dialog)
            and self._attr_dialog.isVisible()
        )
        if vorhanden and not neu_aufbauen and self._attr_dialog_objektart == objektart:
            self._attr_dialog.raise_()
            self._attr_dialog.activateWindow()
            return
        if vorhanden:
            self._attr_dialog.close()

        from .attributaenderungen_dialog import AttributaenderungenDialog
        self._attr_dialog = AttributaenderungenDialog(
            self.iface,
            vergleich["aenderungen"],
            vergleich["crs"],
            self.blink_controller,
            (vergleich["zustand_a_layer"], vergleich["zustand_b_layer"]),
            objektart,
            vergleich["zeitraum"],
            self.iface.mainWindow(),
        )
        self._attr_dialog_objektart = objektart
        self._attr_dialog.setAttribute(Qt.WA_DeleteOnClose)
        self._attr_dialog.show()
        self._attr_dialog.raise_()
        self._attr_dialog.activateWindow()

    def initGui(self):
        self.toolbar = self.iface.addToolBar("NAS2QGIS")
        self.toolbar.setObjectName("NAS2QGIS")

        icon = QIcon(os.path.join(PLUGIN_DIR, "icons", "import.svg"))
        self.oeffnen_action = QAction(icon, "NAS2QGIS öffnen...", self.iface.mainWindow())
        self.oeffnen_action.setToolTip("NAS2QGIS öffnen (Import / Vergleich)")
        self.oeffnen_action.triggered.connect(self.oeffne_hauptdialog)
        self.iface.addPluginToMenu(self.menu, self.oeffnen_action)
        self.toolbar.addAction(self.oeffnen_action)
        self.actions.append(self.oeffnen_action)

    def unload(self):
        self.blink_controller.stoppen()
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
        self.iface.mainWindow().removeToolBar(self.toolbar)
        self.toolbar = None

    def oeffne_hauptdialog(self):
        from .hauptdialog import HauptDialog
        self._hauptdialog = HauptDialog(self, self.iface.mainWindow())
        self._hauptdialog.show()
