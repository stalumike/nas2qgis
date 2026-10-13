"""
Gemeinsame Zeitpunkt-Auswahl (A/B) fuer Vergleich-Reiter und
Wertklassen-Abschnitte.

- Eintraege durchnummeriert mit deutschem Datum:
  "Lieferung 8 – 30.06.2026  (datei.xml)"
- Schnellwahl-Buttons:
    "◀ Paar zurück" / "Paar vor ▶"  - A und B gemeinsam um eine Lieferung
                                       verschieben (Kettenvergleich 1→2, 2→3 ...)
    "B = A + 1"                      - B auf die Lieferung direkt nach A
    "A = B − 1"                      - A auf die Lieferung direkt vor B
  Die Buttons sind immer sichtbar und werden am Rand der Liste ausgegraut.
- Ist A nicht aelter als B, erscheint ein Hinweis und das Signal
  gueltigkeit_geaendert(False) wird gesendet - der Aufrufer graut damit
  seinen Start-Button aus. Es wird bewusst NICHT stillschweigend vertauscht.

Farben: A gruen, B rot (wie "neu"/"entfernt" im Unterschiede-Layer) - nur
die Buchstaben, in Beschriftungen und auf den Buttons. QPushButton kann
selbst kein Rich-Text, deshalb traegt _FarbButton ein QLabel.

Alle programmatischen Aenderungen setzen beide Listen mit blockierten
Signalen und pruefen danach genau einmal - Buttons und Listen koennen sich
so nicht gegenseitig ausloesen.
"""

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


FARBE_A = "#2e7d32"  # gruen
FARBE_B = "#c62828"  # rot


def _buchstaben(farbig=True):
    """{'A': html, 'B': html} - eingefaerbt oder neutral (ausgegraut)."""
    if not farbig:
        return {"A": "A", "B": "B"}
    stil = "font-weight:bold;"
    return {
        "A": f'<span style="color:{FARBE_A};{stil}">A</span>',
        "B": f'<span style="color:{FARBE_B};{stil}">B</span>',
    }


class _FarbButton(QPushButton):
    """Button mit eingefaerbten Buchstaben A/B. vorlage z.B. "{B} = {A} + 1".
    Ist der Button deaktiviert, entfallen die Farben, damit er wie jeder
    andere ausgegraute Button aussieht."""

    def __init__(self, vorlage, parent=None):
        super().__init__(parent)
        self._vorlage = vorlage
        self._label = QLabel()
        self._label.setTextFormat(Qt.RichText)
        self._label.setAlignment(Qt.AlignCenter)
        # Klicks gehen an den Button, nicht ans Label
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents)
        innen = QHBoxLayout(self)
        innen.setContentsMargins(8, 0, 8, 0)
        innen.addWidget(self._label)
        self._darstellen()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self._darstellen()

    def _darstellen(self):
        self._label.setText(self._vorlage.format(**_buchstaben(self.isEnabled())))
        self.setMinimumWidth(self._label.sizeHint().width() + 20)


def _datum_de(iso):
    """'2026-06-30T00:00:00Z' -> '30.06.2026'"""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"


class ZeitpunktAuswahl(QWidget):
    gueltigkeit_geaendert = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.combo_a = QComboBox()
        self.combo_b = QComboBox()

        self.paar_zurueck_button = QPushButton("◀ Paar zurück")
        self.paar_zurueck_button.setToolTip("A und B gemeinsam eine Lieferung zurück")
        self.b_nach_a_button = _FarbButton("{B} = {A} + 1")
        self.b_nach_a_button.setToolTip("Zeitpunkt B auf die Lieferung direkt nach A setzen")
        self.a_vor_b_button = _FarbButton("{A} = {B} − 1")
        self.a_vor_b_button.setToolTip("Zeitpunkt A auf die Lieferung direkt vor B setzen")
        self.paar_vor_button = QPushButton("Paar vor ▶")
        self.paar_vor_button.setToolTip("A und B gemeinsam eine Lieferung weiter")

        button_row = QHBoxLayout()
        for button in (self.paar_zurueck_button, self.b_nach_a_button,
                       self.a_vor_b_button, self.paar_vor_button):
            button_row.addWidget(button)

        self.hinweis_label = QLabel()
        self.hinweis_label.setStyleSheet("color: #c62828;")
        self.hinweis_label.setVisible(False)

        farbig = _buchstaben()
        label_a = QLabel(f"Zeitpunkt {farbig['A']} (älterer Stand):")
        label_b = QLabel(f"Zeitpunkt {farbig['B']} (neuerer Stand):")
        for label in (label_a, label_b):
            label.setTextFormat(Qt.RichText)
        layout.addWidget(label_a, 0, 0)
        layout.addWidget(self.combo_a, 0, 1)
        layout.addLayout(button_row, 1, 1)
        layout.addWidget(label_b, 2, 0)
        layout.addWidget(self.combo_b, 2, 1)
        layout.addWidget(self.hinweis_label, 3, 1)
        layout.setColumnStretch(1, 1)

        # Signal liefert den Index mit - _pruefen braucht ihn nicht
        self.combo_a.currentIndexChanged.connect(lambda _index: self._pruefen())
        self.combo_b.currentIndexChanged.connect(lambda _index: self._pruefen())
        self.paar_zurueck_button.clicked.connect(lambda: self._verschieben(-1, -1))
        self.paar_vor_button.clicked.connect(lambda: self._verschieben(1, 1))
        self.b_nach_a_button.clicked.connect(
            lambda: self._setzen(self.combo_a.currentIndex(), self.combo_a.currentIndex() + 1)
        )
        self.a_vor_b_button.clicked.connect(
            lambda: self._setzen(self.combo_b.currentIndex() - 1, self.combo_b.currentIndex())
        )
        self._pruefen()

    # ------------------------------------------------------------------
    # Oeffentliche Schnittstelle
    # ------------------------------------------------------------------

    def setze_lieferungen(self, lieferungen, index_a=None, index_b=None):
        """lieferungen: [(dateiname, abgabeintervallEnde), ...] chronologisch.
        Vorauswahl: index_a/index_b (negativ = vom Ende gezaehlt);
        ohne Angabe vorletzte und letzte Lieferung."""
        for combo in (self.combo_a, self.combo_b):
            combo.blockSignals(True)
            combo.clear()
        for nr, (dateiname, abgabe_ende) in enumerate(lieferungen, start=1):
            label = f"Lieferung {nr} – {_datum_de(abgabe_ende)}  ({dateiname})"
            self.combo_a.addItem(label, abgabe_ende)
            self.combo_b.addItem(label, abgabe_ende)
        anzahl = len(lieferungen)
        a = index_a if index_a is not None else anzahl - 2
        b = index_b if index_b is not None else anzahl - 1
        self._setzen(a % anzahl if anzahl else -1, b % anzahl if anzahl else -1)

    def setze_zeitpunkte(self, iso_a, iso_b):
        """Auswahl wiederherstellen (z.B. letztes Ergebnis). Unbekannte
        Zeitpunkte lassen die jeweilige Auswahl unveraendert."""
        a = self.combo_a.findData(iso_a)
        b = self.combo_b.findData(iso_b)
        self._setzen(a if a >= 0 else self.combo_a.currentIndex(),
                     b if b >= 0 else self.combo_b.currentIndex())

    def iso_a(self):
        return self.combo_a.currentData()

    def iso_b(self):
        return self.combo_b.currentData()

    def anzahl(self):
        return self.combo_a.count()

    def ist_gueltig(self):
        return self.anzahl() >= 2 and self.combo_a.currentIndex() < self.combo_b.currentIndex()

    # ------------------------------------------------------------------
    # Intern
    # ------------------------------------------------------------------

    def _setzen(self, index_a, index_b):
        anzahl = self.anzahl()
        if anzahl:
            index_a = max(0, min(index_a, anzahl - 1))
            index_b = max(0, min(index_b, anzahl - 1))
        for combo, index in ((self.combo_a, index_a), (self.combo_b, index_b)):
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._pruefen()

    def _verschieben(self, delta_a, delta_b):
        """Paar gemeinsam verschieben - am Rand der Liste gar nicht, statt
        nur eine Seite zu begrenzen (sonst wuerde A = B entstehen)."""
        a = self.combo_a.currentIndex() + delta_a
        b = self.combo_b.currentIndex() + delta_b
        if 0 <= a < self.anzahl() and 0 <= b < self.anzahl():
            self._setzen(a, b)

    def _pruefen(self):
        anzahl = self.anzahl()
        a, b = self.combo_a.currentIndex(), self.combo_b.currentIndex()
        gueltig = self.ist_gueltig()

        self.paar_zurueck_button.setEnabled(gueltig and a > 0)
        self.paar_vor_button.setEnabled(gueltig and b < anzahl - 1)
        self.b_nach_a_button.setEnabled(anzahl >= 2 and 0 <= a < anzahl - 1)
        self.a_vor_b_button.setEnabled(anzahl >= 2 and b > 0)

        if anzahl == 0:
            hinweis = ""
        elif anzahl == 1:
            hinweis = "Für einen Vergleich werden mindestens zwei Lieferungen benötigt."
        elif a == b:
            hinweis = "Zeitpunkt A und B sind identisch."
        elif a > b:
            hinweis = "Zeitpunkt A muss vor Zeitpunkt B liegen."
        else:
            hinweis = ""
        self.hinweis_label.setText(hinweis)
        self.hinweis_label.setVisible(bool(hinweis))
        self.gueltigkeit_geaendert.emit(gueltig)
