# WebUntis Home Assistant Add-ons

Dieses Repository stellt das Add-on **WebUntis Stundenplan** fuer Home Assistant bereit.

## Installation in Home Assistant

1. Dieses Repository nach GitHub pushen.
2. In Home Assistant **Einstellungen > Add-ons > Add-on Store** oeffnen.
3. Rechts oben das Menue oeffnen und **Repositories** waehlen.
4. Die GitHub-URL dieses Repositorys eintragen, zum Beispiel:

   ```text
   https://github.com/Createsince1963/HA_WebUntis
   ```

5. Danach erscheint das Add-on **WebUntis Stundenplan** im Add-on Store.
6. Add-on installieren, konfigurieren und starten.
7. Ueber die Home-Assistant-Seitenleiste **Stundenplan** oeffnen.

## HACS

HACS ist fuer Home-Assistant-Integrationen, Dashboards und Frontend-Karten gedacht. Dieses Projekt ist ein Home-Assistant **Add-on** und wird deshalb direkt ueber den Add-on Store per Repository-URL installiert.

## Add-on

Das Add-on liegt im Ordner:

```text
webuntis_stundenplan/
```

Es nutzt das bestehende WebUntis-Backend aus der APK-Vorlage und bringt eine eigene Web-GUI fuer Home Assistant Ingress mit.

## Demo-Konfiguration

Standardwerte:

```text
Server: demo.local
Schule: demo
Benutzer: Demo
Passwort: Demo
```

Damit kann das Add-on ohne echte WebUntis-Zugangsdaten mit Offline-Beispieldaten gestartet werden.
