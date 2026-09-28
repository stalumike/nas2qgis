"""
Haelt den Zustand des Blinkvergleichs (welche zwei Layer im Wechsel ein-/
ausgeblendet werden) UNABHAENGIG vom Vergleichs-Dialog. Die Plugin-Instanz
besitzt genau ein BlinkController-Objekt, das ueber die gesamte QGIS-Sitzung
bestehen bleibt - dadurch bleibt ein laufender oder pausierter Blinkvergleich
erhalten, auch wenn der Vergleichs-Dialog geschlossen und neu geoeffnet wird.
"""

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.core import QgsProject


class BlinkController(QObject):
    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._umschalten)
        self._layer_a = None
        self._layer_b = None
        self._zeigt_a = True
        self._andere_layer_sichtbarkeit = None  # dict layer_id -> bool, waehrend des Blinkens

    def _layer_sichtbarkeit_setzen(self, layer, sichtbar):
        knoten = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if knoten is not None:
            knoten.setItemVisibilityChecked(sichtbar)

    def hat_gueltige_layer(self):
        if self._layer_a is None or self._layer_b is None:
            return False
        # sip.isdeleted() PRUEFEN, bevor ueberhaupt eine Methode auf den
        # Layer-Objekten aufgerufen wird - selbst .id() stuerzt ab, wenn das
        # zugrundeliegende C++-Objekt (z.B. durch Schliessen/Neuladen des
        # Projekts) bereits zerstoert wurde, waehrend die Python-Referenz
        # noch existiert.
        if sip.isdeleted(self._layer_a) or sip.isdeleted(self._layer_b):
            self._layer_a = None
            self._layer_b = None
            self._andere_layer_sichtbarkeit = None
            return False
        return (
            QgsProject.instance().mapLayer(self._layer_a.id()) is not None
            and QgsProject.instance().mapLayer(self._layer_b.id()) is not None
        )

    def _umschalten(self):
        if not self.hat_gueltige_layer():
            self.stoppen()
            return
        self._zeigt_a = not self._zeigt_a
        self._layer_sichtbarkeit_setzen(self._layer_a, self._zeigt_a)
        self._layer_sichtbarkeit_setzen(self._layer_b, not self._zeigt_a)

    def layer_setzen(self, layer_a, layer_b):
        """Nach einem neuen Vergleich aufrufen - ersetzt die aktuellen
        Blink-Layer und stoppt einen evtl. noch laufenden alten Durchlauf
        (inkl. Wiederherstellen der Sichtbarkeit anderer Layer)."""
        self.stoppen()
        self._layer_a = layer_a
        self._layer_b = layer_b
        self._zeigt_a = True
        self._layer_sichtbarkeit_setzen(layer_a, True)
        self._layer_sichtbarkeit_setzen(layer_b, False)

    def _andere_layer_ausblenden(self):
        """Merkt sich die Sichtbarkeit aller Layer AUSSER den beiden
        Blink-Layern und schaltet sie aus - beim Blinkvergleich soll nur
        Zustand A/B zu sehen sein, kein Quell-Layer/Unterschiede-Layer/etc.
        im Weg."""
        ausnahmen = {self._layer_a.id(), self._layer_b.id()}
        root = QgsProject.instance().layerTreeRoot()
        self._andere_layer_sichtbarkeit = {}
        for layer_id in QgsProject.instance().mapLayers().keys():
            if layer_id in ausnahmen:
                continue
            knoten = root.findLayer(layer_id)
            if knoten is not None:
                self._andere_layer_sichtbarkeit[layer_id] = knoten.itemVisibilityChecked()
                knoten.setItemVisibilityChecked(False)

    def _andere_layer_wiederherstellen(self):
        if self._andere_layer_sichtbarkeit is None:
            return
        root = QgsProject.instance().layerTreeRoot()
        for layer_id, sichtbar in self._andere_layer_sichtbarkeit.items():
            knoten = root.findLayer(layer_id)
            if knoten is not None:
                knoten.setItemVisibilityChecked(sichtbar)
        self._andere_layer_sichtbarkeit = None

    def starten(self, intervall_ms):
        if not self.hat_gueltige_layer():
            return
        if self._andere_layer_sichtbarkeit is None:
            self._andere_layer_ausblenden()
        self._timer.start(intervall_ms)

    def stoppen(self):
        self._timer.stop()
        self._andere_layer_wiederherstellen()

    def intervall_setzen(self, intervall_ms):
        if self._timer.isActive():
            self._timer.start(intervall_ms)

    def ist_aktiv(self):
        return self._timer.isActive()

    def aktuelle_layer_namen(self):
        if not self.hat_gueltige_layer():
            return None
        return self._layer_a.name(), self._layer_b.name()
