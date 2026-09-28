@echo off
chcp 65001 >nul
setlocal

:: ============================================================
:: NAS2QGIS - Plugin-Release bauen
:: ============================================================
:: Layout: dieses Skript liegt im Repo-Root, der Plugin-Ordner
:: "NAS2QGIS" liegt direkt daneben. Build-Ziel = derselbe Ordner
:: (kein separates Test/Prod-Repository wie bei LEFIS).

set ROOT=%~dp0
set PLUGIN_DIR=%ROOT%NAS2QGIS
set REPO_DIR=%ROOT%
set REPO_URL=file:///O:/STALUMSAbt3/LEFIS-Administration/Code/NAS2QGIS

:: Pruefen ob Plugin-Ordner existiert
if not exist "%PLUGIN_DIR%\metadata.txt" (
    echo FEHLER: metadata.txt nicht gefunden in %PLUGIN_DIR%
    pause
    exit /b 1
)

:: Aktuelle Version aus metadata.txt lesen
for /f "tokens=2 delims==" %%a in ('findstr /i "^version=" "%PLUGIN_DIR%\metadata.txt"') do set CURRENT_VERSION=%%a
set CURRENT_VERSION=%CURRENT_VERSION: =%

echo.
echo ============================================================
echo  NAS2QGIS - Release bauen
echo ============================================================
echo  Aktuelle Version: %CURRENT_VERSION%
echo.
set /p NEW_VERSION="  Neue Version eingeben (Enter = unveraendert): "
if "%NEW_VERSION%"=="" set NEW_VERSION=%CURRENT_VERSION%

set ZIP_NAME=nas2qgis_%NEW_VERSION%.zip

echo.
echo  Folgende Aktionen werden ausgefuehrt:
echo   - Version:            %NEW_VERSION%
echo   - Repository:         %REPO_DIR%
echo   - ZIP (versioniert):  %ZIP_NAME%
echo   - ZIP (Repository):   nas2qgis.zip
echo.
set /p CONFIRM="  Fortfahren? (J/N): "
if /i not "%CONFIRM%"=="J" (
    echo  Abgebrochen.
    goto :end
)

:: [1/4] metadata.txt aktualisieren
echo.
echo [1/4] Aktualisiere metadata.txt ...
powershell -Command "$f='%PLUGIN_DIR%\metadata.txt'; $c=[System.IO.File]::ReadAllText($f,[System.Text.Encoding]::UTF8) -replace '(?m)^version=.*','version=%NEW_VERSION%'; [System.IO.File]::WriteAllText($f,$c,[System.Text.UTF8Encoding]::new($false))"
if errorlevel 1 ( echo  FEHLER metadata.txt & goto :error )
echo       OK

:: [2/4] plugins.xml neu schreiben mit korrekter Version
echo [2/4] Aktualisiere plugins.xml ...
(
    echo ^<?xml version="1.0" encoding="UTF-8"?^>
    echo ^<plugins^>
    echo   ^<pyqgis_plugin name="NAS2QGIS" version="%NEW_VERSION%"^>
    echo     ^<description^>Import von NAS/ALKIS-Lieferungen in ein historisiertes GeoPackage und Vergleich zweier Zeitpunkte^</description^>
    echo     ^<version^>%NEW_VERSION%^</version^>
    echo     ^<qgis_minimum_version^>3.16^</qgis_minimum_version^>
    echo     ^<file_name^>nas2qgis.zip^</file_name^>
    echo     ^<author_name^>STALUMS Abt. 3^</author_name^>
    echo     ^<download_url^>%REPO_URL%/nas2qgis.zip^</download_url^>
    echo     ^<experimental^>False^</experimental^>
    echo     ^<deprecated^>False^</deprecated^>
    echo     ^<tags^>alkis,nas,kataster,geopackage^</tags^>
    echo     ^<category^>Vector^</category^>
    echo   ^</pyqgis_plugin^>
    echo ^</plugins^>
) > "%ROOT%plugins.xml"
if errorlevel 1 ( echo  FEHLER plugins.xml & goto :error )
echo       OK

:: [3/4] ZIP erstellen (versioniert, fuers Archiv)
echo [3/4] Erstelle %ZIP_NAME% ...
if exist "%PLUGIN_DIR%\__pycache__" rmdir /s /q "%PLUGIN_DIR%\__pycache__"
if exist "%ROOT%%ZIP_NAME%" del "%ROOT%%ZIP_NAME%"
powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::CreateFromDirectory('%PLUGIN_DIR%', '%ROOT%%ZIP_NAME%', [System.IO.Compression.CompressionLevel]::Optimal, $true)"
if errorlevel 1 ( echo  FEHLER ZIP & goto :error )
echo       OK

:: [4/4] Feste Repository-ZIP aktualisieren (von plugins.xml referenziert)
echo [4/4] Aktualisiere nas2qgis.zip im Repository ...
copy /Y "%ROOT%%ZIP_NAME%" "%ROOT%nas2qgis.zip" >nul
if errorlevel 1 ( echo  FEHLER beim Kopieren der ZIP & goto :error )
echo       OK

:done
echo.
echo ============================================================
echo  Release %NEW_VERSION% erfolgreich erstellt!
echo  Versionierte ZIP: %ROOT%%ZIP_NAME%
echo  Repository-ZIP:   %ROOT%nas2qgis.zip
echo  plugins.xml:      %ROOT%plugins.xml
echo ============================================================
goto :end

:error
echo.
echo ============================================================
echo  Release fehlgeschlagen!
echo ============================================================

:end
echo.
pause
endlocal
