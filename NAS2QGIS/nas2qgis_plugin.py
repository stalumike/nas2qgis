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
        self.letzte_attributaenderungen = None  # (liste, crs) vom letzten Vergleich - ueberlebt Dialog schliessen
        self.letztes_gpkg = None  # zuletzt gewaehltes Ziel-GeoPackage - ueberlebt Dialog schliessen
        self.letzter_vergleich = None  # {"objektart","diff_layer","zustand_a_layer","zustand_b_layer","crs"}

    def zeige_attributaenderungen(self):
        """Oeffnet das Attributaenderungen-Fenster erneut mit dem Ergebnis
        des letzten Vergleichs - unabhaengig davon, ob der Hauptdialog
        selbst noch offen ist."""
        from qgis.PyQt.QtWidgets import QMessageBox

        if self.letzte_attributaenderungen is None:
            QMessageBox.information(
                self.iface.mainWindow(), "Keine Daten",
                "Es wurde noch kein Vergleich mit Attributänderungen durchgeführt."
            )
            return
        aenderungen, crs = self.letzte_attributaenderungen
        from .attributaenderungen_dialog import AttributaenderungenDialog
        self._attr_dialog = AttributaenderungenDialog(
            self.iface, aenderungen, crs, self.blink_controller, self.iface.mainWindow()
        )
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
