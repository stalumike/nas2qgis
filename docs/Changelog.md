# Changelog QGIS Plugin NAS2QGIS

Alle wichtigen Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

---
## [unrelesead]
## [1.0.1] 2026-09-29
### Added
- Prüfungen zum Einspielen der NBA Daten ergänzt:
- geprüft wird auf richtige Verfahrenszugehörigkeit
- geprüft wird bei bereits im gpkg vorhandenen NBA Daten ob die einzuspielenden Daten neuer als die vorhandenen sind
- doppeltes Einspielen der selben Daten wird gemeldet und verhindert
- Meldungen beim einspielen der Daten verbessert
## [1.0.0] 2026-09-28
- erste Version des Plugins
---

<!--
KATEGORIEN (nur verwenden was zutrifft, Rest weglassen):

### Added      – Neue Funktionen
### Changed    – Änderungen an bestehenden Funktionen
### Deprecated – Funktionen die bald entfernt werden
### Removed    – Entfernte Funktionen
### Fixed      – Bugfixes
### Security   – Sicherheitslücken geschlossen

RELEASE-CHECKLISTE:
1. [Unreleased] umbenennen zu [x.y.z] - YYYY-MM-DD
2. Neuen leeren [Unreleased]-Block oben einfügen
3. git add CHANGELOG.md
4. git commit -m "chore: release x.y.z"
5. git tag vx.y.z
6. git push origin main --tags

Commit Messages strukturieren
Präfix              Bedeutung                                       Beispiel
feat:               Neue Funktion                                   feat: Exportfunktion hinzugefügt
fix:                Bugfix                                          fix: Absturz beim Speichern behoben
chore:              Wartung/Housekeeping, kein Feature, kein Fix    chore: release 1.3.0
docs:               Nur Dokumentation                               docs: README aktualisiert
refactor:           Code umgebaut, Verhalten gleich                 refactor: Datenbanklogik aufgeräumt
style:              Formatierung, keine Logik                       style: Einrückung korrigiert

Push zu Github:
# 1. In dein lokales Repo-Verzeichnis wechseln
cd /pfad/zu/deinem/repo

# 2. GitHub als zweiten Remote hinzufügen
git remote add github https://github.com/dein-user/dein-repo.git

# 3. Alles zu GitHub pushen (mit --force, weil der Stand dort veraltet ist)
git push github --all --force
git push github --tags --force
-->
