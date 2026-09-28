"""
Ermittelt je Flurstueck den Beteiligtenstatus relativ zum "eigenen"
Verfahrensgebiet (AX_BauRaumOderBodenordnungsrecht, kurz BRB).

Hintergrund: Manche NBA-Lieferungen enthalten mehrere BRB-Objekte - entweder
durch angrenzende Fremdverfahren, oder weil manche Katasteraemter das
eigene BRB flurweise in mehrere Teilobjekte aufteilen (an echten Daten
gesehen: 19 Teilobjekte fuers eigene Verfahren + 3 fuer ein Nachbarverfahren,
alle im selben Lieferungspaket). Ein Textabgleich ueber den Lieferungskopf
(z.B. antragsnummer) ist nicht zuverlaessig, da nicht alle Katasteraemter
den Kopf gleich aufbauen.

Ansatz (zweistufig):
1. BRB-Objekte werden anhand des Attributs 'bezeichnung' (Verfahrensnummer)
   zu Gruppen zusammengefasst - fehlt 'bezeichnung' bei einem Objekt, bildet
   es eine eigene Einzelgruppe (Rueckfall auf das alte Verhalten fuer genau
   dieses Objekt, kein harter Fehler).
2. Die Gruppe mit den meisten aktuell ueberschneidenden Flurstuecken gilt
   als das eigene Verfahrensgebiet. Das ist bewusst dasselbe Kriterium wie
   zuvor (an echten Daten gegen eine LEFIS-Referenzliste exakt verifiziert),
   nur jetzt auf Gruppen- statt auf Einzelobjekt-Ebene angewendet - robuster
   als reines Zaehlen der BRB-*Teilobjekte* je Gruppe, da ein zufaelliger
   Gleichstand bei der Flurstuecksanzahl praktisch ausgeschlossen ist,
   waehrend die Anzahl an BRB-Teilstuecken von der (beliebigen)
   Aufteilungsgranularitaet des jeweiligen Katasteramts abhaengt.

Status je Flurstueck:
  - "beteiligt"      : Geometrie ueberschneidet die Verfahrensgruppe flaechig
  - "nebenbeteiligt" : Geometrie beruehrt die Verfahrensgruppe nur (angrenzend,
                        keine Flaechenueberschneidung)
  - "nicht beteiligt": alles andere

Ergebnis wird als neue Spalte 'beteiligtenstatus' direkt in die
AX_Flurstueck-Tabelle des GeoPackage geschrieben (fuer JEDE Version/Zeile,
nicht nur den aktuellen Stand - die Einordnung ist rein geometrisch und
zeitunabhaengig).
"""

import sqlite3

from qgis.core import QgsFeatureRequest, QgsVectorLayer

BRB_TABELLE = "AX_BauRaumOderBodenordnungsrecht"
FLURSTUECK_TABELLE = "AX_Flurstueck"


def _tabelle_vorhanden(gpkg_path, tabelle):
    conn = sqlite3.connect(gpkg_path)
    vorhanden = conn.execute(
        "SELECT 1 FROM gpkg_contents WHERE table_name = ?", (tabelle,)
    ).fetchone() is not None
    conn.close()
    return vorhanden


def _bezeichnung_lesen(feature):
    idx = feature.fields().indexOf("bezeichnung")
    if idx < 0:
        return None
    wert = feature["bezeichnung"]
    if wert in (None, ""):
        return None
    return str(wert)


def berechne_beteiligtenstatus(gpkg_path, log=None):
    """log: optionale Callback-Funktion(text) fuers Protokoll im Dialog."""
    def _log(text):
        if log:
            log(text)

    def _sichere_geom(geom, kontext):
        """Baut eine Geometrie robust neu auf, bevor sie an GEOS-Operationen
        (intersects/touches) uebergeben wird. 'buffer(0)' erzwingt eine
        komplette Neuberechnung ueber einen aelteren, robusteren GEOS-Codepfad
        als makeValid()/intersects() - das ist der Standard-Workaround fuer
        Abstuerze (access violation) im neueren RelateNG-Algorithmus, den
        makeValid() allein nicht immer verhindert."""
        if geom is None or geom.isEmpty():
            return None
        try:
            bereinigt = geom.buffer(0, 8)
        except Exception:
            bereinigt = None
        if bereinigt is None or bereinigt.isEmpty():
            bereinigt = geom.makeValid()
        if bereinigt is None or bereinigt.isEmpty():
            _log(f"WARNUNG: Geometrie ({kontext}) konnte nicht bereinigt werden - wird uebersprungen.")
            return None
        return bereinigt

    if not _tabelle_vorhanden(gpkg_path, BRB_TABELLE):
        _log(f"Keine Tabelle '{BRB_TABELLE}' im GeoPackage gefunden - Beteiligtenstatus wird uebersprungen.")
        return
    if not _tabelle_vorhanden(gpkg_path, FLURSTUECK_TABELLE):
        _log(f"Keine Tabelle '{FLURSTUECK_TABELLE}' im GeoPackage gefunden - Beteiligtenstatus wird uebersprungen.")
        return

    brb_layer = QgsVectorLayer(f"{gpkg_path}|layername={BRB_TABELLE}", "brb", "ogr")
    flurstueck_layer = QgsVectorLayer(f"{gpkg_path}|layername={FLURSTUECK_TABELLE}", "flurstueck", "ogr")
    if not brb_layer.isValid() or not flurstueck_layer.isValid():
        _log("BRB- oder Flurstueck-Layer konnte nicht geoeffnet werden - Beteiligtenstatus wird uebersprungen.")
        return

    brb_aktuell = list(brb_layer.getFeatures(QgsFeatureRequest().setFilterExpression('"gueltig_bis" IS NULL')))
    if not brb_aktuell:
        _log("Kein aktuell gueltiges BRB-Objekt gefunden - Beteiligtenstatus wird uebersprungen.")
        return

    brb_geometrien = []  # Liste von (feature, geom)
    for brb in brb_aktuell:
        geom = _sichere_geom(brb.geometry(), f"BRB oid={brb['oid']}")
        if geom is not None:
            brb_geometrien.append((brb, geom))
    if not brb_geometrien:
        _log("Kein auswertbares BRB-Objekt (Geometrie leer/ungueltig) - Beteiligtenstatus wird uebersprungen.")
        return

    # BRB-Teile anhand 'bezeichnung' zu Gruppen zusammenfassen. Fehlt die
    # bezeichnung, bildet das Objekt eine eigene Einzelgruppe (ueber seine OID).
    gruppen = {}  # schluessel -> Liste[(feature, geom)]
    for brb, geom in brb_geometrien:
        bezeichnung = _bezeichnung_lesen(brb)
        schluessel = ("bezeichnung", bezeichnung) if bezeichnung else ("oid", brb["oid"])
        gruppen.setdefault(schluessel, []).append((brb, geom))

    flurstuecke_aktuell = list(
        flurstueck_layer.getFeatures(QgsFeatureRequest().setFilterExpression('"gueltig_bis" IS NULL'))
    )
    # Geometrien der aktuellen Flurstuecke einmalig validieren/cachen (werden
    # fuer jede Gruppe erneut gebraucht, um die Ueberschneidungen zu zaehlen)
    aktuell_geom_cache = {}
    for f in flurstuecke_aktuell:
        aktuell_geom_cache[f.id()] = _sichere_geom(f.geometry(), f"Flurstueck fid={f.id()}")

    # Je Gruppe zaehlen, wie viele UNTERSCHIEDLICHE aktuelle Flurstuecke
    # irgendeines der Gruppen-Teilstuecke ueberschneiden (nicht die Anzahl
    # BRB-Teilstuecke selbst - das waere anfaelliger fuer zufaellige
    # Gleichstaende, siehe Modulbeschreibung oben).
    beste_gruppe_geoms = None
    beste_anzahl = -1
    uebersicht = []
    for schluessel, mitglieder in gruppen.items():
        geoms = [g for _, g in mitglieder]
        betroffene_fids = set()
        for f in flurstuecke_aktuell:
            fgeom = aktuell_geom_cache.get(f.id())
            if fgeom is None:
                continue
            fbbox = fgeom.boundingBox()
            for g in geoms:
                if not g.boundingBox().intersects(fbbox):
                    continue  # reiner Zahlenvergleich, kein GEOS-Aufruf noetig
                if g.intersects(fgeom):
                    betroffene_fids.add(f.id())
                    break
        anzahl = len(betroffene_fids)
        uebersicht.append((schluessel, len(mitglieder), anzahl))
        if anzahl > beste_anzahl:
            beste_anzahl = anzahl
            beste_gruppe_geoms = geoms

    if len(gruppen) > 1:
        _log(f"{len(brb_geometrien)} BRB-Objekte in {len(gruppen)} Gruppe(n) (nach 'bezeichnung') gefunden:")
        for schluessel, anzahl_teile, anzahl_flurst in sorted(uebersicht, key=lambda t: -t[2]):
            art, wert = schluessel
            label = f"bezeichnung={wert}" if art == "bezeichnung" else f"oid={wert} (keine bezeichnung vorhanden)"
            _log(f"  {label}: {anzahl_teile} BRB-Teil(e), {anzahl_flurst} überschneidende Flurstücke")
        _log(f"Verfahrensgebiet anhand der meisten überschneidenden Flurstücke gewählt ({beste_anzahl}).")
    else:
        _log(f"1 BRB-Gruppe gefunden ({len(brb_geometrien)} Teilobjekt(e)), {beste_anzahl} Flurstücke darin.")

    # Status fuer ALLE Zeilen/Versionen berechnen (nicht nur aktuelle) -
    # die Einordnung ist rein geometrisch, nicht zeitabhaengig. Ein Flurstueck
    # gilt als "beteiligt", sobald es IRGENDEIN Teilstueck der Gewinner-Gruppe
    # flaechig ueberschneidet - "nebenbeteiligt" nur, wenn es keins flaechig
    # ueberschneidet, aber mindestens eins beruehrt.
    alle_flurstuecke = list(flurstueck_layer.getFeatures())
    updates = []
    for f in alle_flurstuecke:
        geom = _sichere_geom(f.geometry(), f"Flurstueck fid={f.id()}")
        status = "nicht beteiligt"
        if geom is not None:
            fbbox = geom.boundingBox()
            beruehrt_nur = False
            for vg in beste_gruppe_geoms:
                if not vg.boundingBox().intersects(fbbox):
                    continue
                if not vg.intersects(geom):
                    continue
                if vg.touches(geom):
                    beruehrt_nur = True
                else:
                    status = "beteiligt"
                    break
            if status != "beteiligt" and beruehrt_nur:
                status = "nebenbeteiligt"
        updates.append((f.id(), status))

    conn = sqlite3.connect(gpkg_path)
    try:
        conn.execute(f'ALTER TABLE "{FLURSTUECK_TABELLE}" ADD COLUMN beteiligtenstatus TEXT')
    except sqlite3.OperationalError:
        pass  # Spalte existiert schon (erneuter Lauf)
    conn.executemany(
        f'UPDATE "{FLURSTUECK_TABELLE}" SET beteiligtenstatus = ? WHERE fid = ?',
        [(status, fid) for fid, status in updates],
    )
    conn.commit()
    conn.close()

    anzahl_beteiligt = sum(1 for _, s in updates if s == "beteiligt")
    anzahl_neben = sum(1 for _, s in updates if s == "nebenbeteiligt")
    anzahl_nicht = sum(1 for _, s in updates if s == "nicht beteiligt")
    _log(
        f"Beteiligtenstatus gesetzt: {anzahl_beteiligt} beteiligt, "
        f"{anzahl_neben} nebenbeteiligt, {anzahl_nicht} nicht beteiligt "
        f"(gesamt {len(updates)} Zeilen/Versionen)."
    )
