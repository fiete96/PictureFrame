# WLAN-Verbindung wiederherstellen

## Problem
Nach einem Update funktioniert die WLAN-Verbindung nicht mehr. Der Fehler lautet:
```
Error Connection activation failed: The Wifi network could not be found.
```

## Lösung (OHNE Neuinstallation)

### Option 1: Über Ethernet/HDMI (Empfohlen)

1. **Pi mit Ethernet-Kabel oder HDMI-Monitor verbinden**

2. **SSH-Verbindung herstellen** (falls Ethernet verfügbar):
   ```bash
   ssh pi@<IP-ADRESSE>
   ```

3. **Oder direkt am Pi arbeiten** (mit Tastatur/Monitor)

4. **WLAN-Verbindungen prüfen**:
   ```bash
   nmcli connection show
   ```

5. **Verfügbare WLAN-Netzwerke scannen**:
   ```bash
   sudo nmcli device wifi rescan
   sudo nmcli device wifi list
   ```

6. **Neue WLAN-Verbindung erstellen**:
   ```bash
   sudo nmcli device wifi connect "WLAN-NAME" password "WLAN-PASSWORT"
   ```

7. **Oder über die GUI des Picture Frames**:
   - Auf dem Bildschirm tippen
   - Menü öffnen
   - WLAN-Einstellungen öffnen
   - Netzwerk auswählen und verbinden

### Option 2: Über die GUI (wenn Bildschirm verfügbar)

1. **Auf dem Bildschirm tippen** um das Menü zu öffnen
2. **WLAN-Einstellungen** öffnen
3. **Verfügbare Netzwerke scannen** (Button "Netzwerke scannen")
4. **WLAN-Netzwerk auswählen** und Passwort eingeben
5. **Verbinden**

### Option 3: Über die Web-Interface (wenn Pi über Ethernet erreichbar)

1. **Pi über Ethernet verbinden** (falls möglich)
2. **IP-Adresse ermitteln**:
   ```bash
   hostname -I
   ```
3. **Web-Interface öffnen**: `http://<IP-ADRESSE>`
4. **WLAN-Einstellungen** über das Web-Interface konfigurieren

### Option 4: Manuelle Konfiguration über nmcli

```bash
# 1. Alte Verbindungen löschen (falls nötig)
sudo nmcli connection delete "WLAN-NAME"

# 2. Neue Verbindung erstellen
sudo nmcli device wifi connect "WLAN-NAME" password "WLAN-PASSWORT" name "WLAN-NAME"

# 3. Autoconnect aktivieren
sudo nmcli connection modify "WLAN-NAME" connection.autoconnect yes
sudo nmcli connection modify "WLAN-NAME" connection.autoconnect-priority 10

# 4. Power Management deaktivieren
sudo nmcli connection modify "WLAN-NAME" wifi.powersave 2

# 5. Verbindung aktivieren
sudo nmcli connection up "WLAN-NAME"
```

## Warum ist das passiert?

Das Update hat möglicherweise die NetworkManager-Konfiguration beeinflusst. Die WLAN-Verbindung wird nicht über `config.yaml` verwaltet, sondern direkt über NetworkManager (`nmcli`). 

Die `config.yaml` enthält nur die WLAN-Einstellungen für die UI-Anzeige, aber die tatsächliche Verbindung wird vom System verwaltet.

## Prävention

Ab sofort wird `config.yaml` beim Update automatisch geschützt und nicht überschrieben. Die WLAN-Verbindung sollte stabil bleiben, da sie vom System (NetworkManager) verwaltet wird und nicht von der Anwendung.

