# NAS2QGIS – Historienvergleich und Wertklassenflächen-Ermittlung

Diese Beschreibung richtet sich an Anwender des Plugins (nicht an
Entwickler) und erklärt, was beim Vergleichen zweier Zeitpunkte und beim
Ermitteln der betroffenen Wertklassenflächen genau passiert.

---

## Voraussetzung für beides

Ihr braucht ein GeoPackage, das über **Erweiterungen → NAS2QGIS → NAS-Daten
importieren...** aus euren NAS-Lieferungen aufgebaut wurde. Jedes Objekt
(z. B. jedes Flurstück) wird darin **historisiert** gespeichert – das
heißt, jede Version eines Objekts bleibt erhalten, mit einem Zeitraum, in
dem genau diese Version gültig war. Dadurch lässt sich der Bestand zu
**jedem beliebigen Zeitpunkt** rekonstruieren, nicht nur zum aktuellen
Stand.

---

## Historie vergleichen

**Erweiterungen → NAS2QGIS → Historie vergleichen...**

### Was ihr auswählt

- Einen Layer aus dem GeoPackage (z. B. `AX_Flurstueck`)
- Zwei Zeitpunkte (**Zeitpunkt A** = älterer Stand, **Zeitpunkt B** =
  neuerer Stand) – zur Auswahl stehen die tatsächlich importierten
  Lieferungen, nicht ein frei einzutippendes Datum

### Was danach passiert

Es werden vier Ebenen/Fenster erzeugt:

1. **Zustand A** – der komplette Bestand zum älteren Zeitpunkt
2. **Zustand B** – der komplette Bestand zum neueren Zeitpunkt
3. **Unterschiede** – nur die Objekte, die sich zwischen A und B
   tatsächlich verändert haben. Jedes Objekt bekommt einen Status:
   - `neu` – Objekt existierte in A noch nicht
   - `entfernt` – Objekt existiert in B nicht mehr
   - `geometrie_geaendert` – Lage/Form hat sich geändert, Attribute nicht
   - `attribute_geaendert` – nur Sachdaten geändert, Geometrie identisch
   - `geometrie_und_attribute_geaendert` – beides geändert

   Bei geometrischen Änderungen zeigt dieser Layer **nicht** die komplette
   (unveränderte) Fläche, sondern nur den **tatsächlich verschobenen
   Bereich** (die Differenzfläche zwischen alter und neuer Kontur) – so
   seht ihr auf einen Blick, wo genau sich etwas verändert hat, statt zwei
   fast identische Flächen übereinander vergleichen zu müssen.

4. **Attributänderungen-Fenster** – öffnet sich automatisch, sofern es
   Attributänderungen gibt. Zeigt je geändertem Objekt alle betroffenen
   Felder mit altem und neuem Wert. Von dort aus:
   - **Auf ausgewähltes Objekt zoomen**
   - **Objekt aufleuchten lassen** (kurzes rotes Blinken auf der Karte,
     ohne die Kartenansicht zu verschieben)
   - **OID kopieren**
   - **Blinkvergleich starten/stoppen** (siehe unten)

### Der Blinkvergleich

Im Attributänderungen-Fenster gibt es einen Button **"Blinkvergleich
starten"**. Er blendet Zustand A und Zustand B im schnellen Wechsel ein/aus
(nur einer der beiden ist jeweils sichtbar). Dadurch "springt" die Kontur
sichtbar zwischen alter und neuer Lage hin und her – Veränderungen fallen
so deutlich leichter ins Auge als beim ruhigen Nebeneinander-Betrachten.
Alle anderen Layer werden dafür automatisch ausgeblendet und beim Stoppen
wieder in ihren ursprünglichen Zustand zurückversetzt. Der Takt (wie
schnell umgeschaltet wird) lässt sich einstellen.

Der Blinkvergleich läuft weiter, auch wenn ihr das Vergleichs- oder
Attributänderungen-Fenster schließt – er lässt sich jederzeit über den
Button **"Attributänderungen anzeigen"** im Vergleichs-Dialog wieder
erreichen.

---

## Wertklassenflächen ermitteln

**Erweiterungen → NAS2QGIS → Wertklassenflächen ermitteln...**

Dient dazu, nach einer Flurstücks-Homogenisierung herauszufinden, welche
Wertklassenflächen potenziell angepasst werden müssen, weil sich eine
Flurstücksgrenze verschoben hat.

### Voraussetzungen (werden automatisch geprüft)

1. Es muss **vorher** ein Vergleich für **AX_Flurstueck** durchgeführt
   worden sein (siehe oben) – ein Vergleich einer anderen Objektart zählt
   nicht.
2. Ein Layer mit den Wertklassenflächen muss im Projekt geladen sein. Diese
   Daten liefert das Plugin **nicht** selbst – ihr besorgt sie euch über
   euer separates `lefistogeopackage`-Plugin und ladet sie vorher in QGIS.

Fehlt eine der beiden Voraussetzungen, bekommt ihr eine Meldung, die genau
sagt, was noch fehlt – statt eines leeren oder falschen Ergebnisses.

### Was genau verschnitten wird

**Wichtig für die Interpretation der Ergebnisse:** Es wird nicht die
komplette Flurstücksfläche mit den Wertklassenflächen verschnitten,
sondern **nur der tatsächlich geometrisch veränderte Bereich** (derselbe
schmale Streifen, der auch im "Unterschiede"-Layer des Vergleichs zu sehen
ist). Das ist fachlich der richtige Maßstab: Nur dort, wo sich die Grenze
wirklich verschoben hat, kann eine Wertklassenfläche überhaupt betroffen
sein.

Flurstücke mit **reinen Attributänderungen** (keine Geometrieänderung)
werden dabei **nicht** berücksichtigt – dort gibt es geometrisch nichts,
das eine Wertklassenfläche berühren könnte.

### Ergebnisdarstellung

Ein Fenster mit einer Baumansicht:

- **Oberste Ebene**: ein Eintrag je geändertem Flurstück (OID)
- **Darunter**: die Wertklassenflächen, die von der Änderung dieses
  Flurstücks berührt werden, mit UUID und der Spalte "Nutzung Wertklasse"
  (Nutzungsart + Wertklassennummer zusammen, z. B. "A 38")

Aus dem Fenster heraus:

- **Auf Auswahl zoomen** / **Auswahl aufleuchten lassen**: funktioniert auf
  beiden Ebenen. Beim Flurstück wird dabei – anders als bei der eigentlichen
  Verschneidung – die **komplette aktuelle Flurstücksfläche** angezeigt
  (nicht nur der dünne Änderungsstreifen), damit man auf der Karte
  überhaupt etwas Sichtbares hat, an dem man sich orientieren kann. Bei
  einer Wertklassenfläche wird deren eigene Fläche gezoomt/hervorgehoben.
- **OID/UUID kopieren**: kopiert je nach ausgewählter Ebene die
  Flurstücks-OID oder die Wertklassenflächen-UUID in die Zwischenablage
- **Exportieren...**: schreibt den kompletten Inhalt des Fensters als
  CSV-Datei (eine Zeile je Flurstück-Wertklassenfläche-Paar)

### Wenn keine betroffenen Flächen gefunden werden

Das ist kein Fehler – entweder gab es beim letzten Vergleich keine
geometrischen Änderungen, oder die geänderten Bereiche liegen räumlich
außerhalb der geladenen Wertklassenflächen (z. B. falsches Gebiet geladen).
