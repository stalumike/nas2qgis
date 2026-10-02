"""
Ein Fenster, zwei Reiter - fasst Import und Vergleich unter einer
gemeinsamen Schaltflaeche zusammen. Der Wertklassen-Abschnittsvergleich ist
(vorerst, bis zur Dialog-Konsolidierung) ein Button im Vergleich-Reiter
(siehe vergleich_dialog.py) und oeffnet ein eigenes Fenster. Er setzt keinen
vorherigen Vergleich voraus.
"""

from qgis.PyQt.QtWidgets import QDialog, QTabWidget, QVBoxLayout

from .import_dialog import ImportTab
from .vergleich_dialog import VergleichTab


class HauptDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("NAS2QGIS")
        self.setMinimumSize(640, 600)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.import_tab = ImportTab(plugin, self)
        self.vergleich_tab = VergleichTab(plugin.iface, plugin, self)
        self.tabs.addTab(self.import_tab, "Import")
        self.tabs.addTab(self.vergleich_tab, "Vergleich")
        self.tabs.currentChanged.connect(self._tab_gewechselt)
        layout.addWidget(self.tabs)

    def _tab_gewechselt(self, index):
        # Beide Reiter teilen sich das GeoPackage ueber plugin.letztes_gpkg.
        # Beim Wechsel jeweils abgleichen: der Vergleich-Reiter liest dabei
        # auch Objektarten und Lieferungen neu ein (z.B. direkt nach einem
        # Import), der Import-Reiter uebernimmt ein im Vergleich gewaehltes
        # GeoPackage.
        widget = self.tabs.widget(index)
        if widget is self.vergleich_tab:
            self.vergleich_tab.quelle_aktualisieren()
        elif widget is self.import_tab:
            self.import_tab.gpkg_aktualisieren()
