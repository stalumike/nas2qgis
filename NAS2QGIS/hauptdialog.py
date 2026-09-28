"""
Ein Fenster, zwei Reiter - fasst Import und Vergleich unter einer
gemeinsamen Schaltflaeche zusammen. Die Wertklassenflaechen-Ermittlung ist
bewusst kein dritter Reiter, sondern ein Button im Vergleich-Reiter (siehe
vergleich_dialog.py), da sie einen bereits durchgefuehrten Vergleich
voraussetzt - kein eigenstaendiger Einstiegspunkt.
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
        # Der Vergleich-Reiter wird beim Oeffnen des Hauptdialogs einmalig
        # aufgebaut - moeglicherweise BEVOR im Import-Reiter ueberhaupt ein
        # Layer geladen wurde. Bei jedem Wechsel auf diesen Reiter die
        # Layer-Liste neu einlesen, damit zwischenzeitlich geladene Layer
        # (egal ob ueber den Import-Reiter oder anders) beruecksichtigt werden.
        if self.tabs.widget(index) is self.vergleich_tab:
            self.vergleich_tab.layer_liste_befuellen()
