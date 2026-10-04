# Untis-HUB Add-on

Home Assistant Add-on auf Basis der vorhandenen `Untis-HUB` APK/GUI-Basis im Workspace `APK WEBUNTIS`.

Die APK-Quelle bleibt unverändert/read-only. Dieses Paket bringt eine eigene Web-GUI mit, die das dunkle Navy/Lime-Design, Karten, Chips, Wochenleiste und Stundenplan-Kacheln der APK/PySide6-GUI nachbildet.

Basis:

- Backend: aus dem aktuellen `APK WEBUNTIS` Projekt uebernommen und fuer Home Assistant angepasst.
- GUI: neue Home-Assistant-Web-GUI fuer Ingress.
- Win11/PySide6-App: nur als Designreferenz gelesen, nicht als Laufzeitbasis.
- Add-on-Version `0.4.0`: basiert auf `Untis-HUB_0.4.0.apk`.
- APK-Pruefung: siehe `Untis-HUB_0.4.0.info.txt` und `Untis-HUB_0.4.0.apk.sha256`.
- Logo/Icon: aus `Logo/` der APK-Basis uebernommen.

Uebernommene Datenbereiche aus APK 0.3:

- Stundenplan mit Tag-/Wochenansicht
- Vertretungen
- Pruefungen
- Aufgaben
- Nachrichten
- Ferien
- Lehrer und Lehrer-Stunden aus dem eigenen Stundenplan
- Schulsuche mit Server/LoginName-Auswahl
- Einstellungen fuer Sprache und Wochenlayout
- Stadt-/Ortssuche fuer Schulen mit Uebernahme von WebUntis-Server, Schul-Anzeigename und LoginName
- Direkter Zugriff im Heimnetz ueber `http://<HA-IP>:8099` zusaetzlich zu Home-Assistant-Ingress

Nicht 1:1 uebernommen, weil Home Assistant statt Android laeuft:

- Android-Push-Benachrichtigungen
- Android-Wecker/Wake-Alarm

## Installation

1. Ordner `webuntis-ha-addon` in ein lokales Home-Assistant-Add-on-Repository kopieren.
2. In Home Assistant unter **Einstellungen > Add-ons > Add-on Store > Repositories** das Repository einbinden.
3. Add-on installieren.
4. Zugangsdaten in der Add-on-Konfiguration setzen.
5. Add-on starten und über die Seitenleiste **Stundenplan** öffnen.

## Demo

Standardkonfiguration nutzt den eingebauten Offline-Demozugang:

- Server: `demo.local`
- Schule: `demo`
- Benutzer: `Demo`
- Passwort: `Demo`

## API-Kompatibilität

Die Web-GUI nutzt dieselben Endpunkte wie die APK:

- `POST /api/auth/validate`
- `GET /api/users/my/timetable`
- `GET /api/data/substitutions`
- `GET /api/data/holidays`
- `GET /api/data/teachers`
- `GET /api/data/exams`
- `GET /api/data/homework`
- `GET /api/data/messages`

## Hinweis

Home Assistant Add-ons laufen als Container. Die GUI ist daher eine Web-GUI über Ingress, keine native Android- oder PySide6-Oberfläche.
