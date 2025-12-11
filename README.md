# Picture Frame

Ein vollständiges Embedded-System für einen digitalen Bilderrahmen auf Raspberry Pi Zero 2W mit 1024x600 Pixel HDMI Touchscreen.

**Version:** 1.0.0 (Production Ready)

## Features

- 🖼️ **Automatische Slideshow** mit Touch-Gesten (Swipe links/rechts)
- 📧 **Email-Integration** - Empfang von Bildern per Email
- 🖥️ **Webinterface** - Remote-Verwaltung über Browser
- ⚙️ **Einstellungen** - WLAN, Email, Slideshow-Verhalten
- 🎨 **Bildoptimierung** - Automatische Konvertierung für optimalen Bildschirm
- 🚀 **Custom Bootscreen** - Professioneller Startbildschirm
- 👴 **Großeltern-kompatibel** - Einfache, intuitive Bedienung

## Hardware-Anforderungen

- Raspberry Pi Zero 2W
- 1024x600 Pixel HDMI Touchscreen
- MicroSD-Karte (mindestens 16GB empfohlen)
- Netzteil (5V, mindestens 2.5A)

## Installation

### 1. System-Vorbereitung

**Option A: Raspberry Pi OS Desktop (Empfohlen)**
- Einfachste Installation, alles bereits vorinstalliert
- Download: [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- Wählen Sie "Raspberry Pi OS (32-bit)" mit Desktop

**Option B: Raspberry Pi OS Lite (Minimal)**
- Leichtgewichtiger, benötigt zusätzliches Setup
- Download: [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
- Wählen Sie "Raspberry Pi OS Lite (32-bit)"
- Nach Installation X11-Setup ausführen (siehe unten)

#### Setup für Raspberry Pi OS Lite:

```bash
# Nach der ersten Installation von Raspberry Pi OS Lite:
chmod +x setup_lite.sh
./setup_lite.sh
```

**Wichtig für beide Varianten:**
- Touchscreen-Treiber installieren (je nach Modell)
- SSH aktivieren (optional, aber empfohlen)
- WLAN konfigurieren

### 2. Projekt installieren

```bash
# Repository klonen oder Dateien kopieren
cd ~
git clone <repository-url> PictureFrameV3
cd PictureFrameV3

# Installation ausführen
chmod +x install.sh
./install.sh
```

### 3. Konfiguration

Bearbeiten Sie `config.yaml`:

```yaml
email:
  imap_server: "imap.gmail.com"  # Ihr Email-Provider
  username: "ihre-email@gmail.com"
  password: "ihr-passwort"
  check_interval_minutes: 5
  auto_reply: true
  reply_message: "Bild erfolgreich empfangen!"

slideshow:
  interval_seconds: 10
  shuffle: false
  loop: true
```

### 4. Starten

**Manuell:**
```bash
python3 src/main.py
```

**Als Service (Autostart):**
```bash
sudo systemctl start pictureframe
sudo systemctl enable pictureframe  # Autostart aktivieren
```

## Bedienung

### Touchscreen

- **Swipe nach rechts**: Vorheriges Bild
- **Swipe nach links**: Nächstes Bild
- **Langes Drücken (2 Sekunden)**: Menü öffnen

### Menü

- **Slideshow**: Zurück zur Bildanzeige
- **Bildverwaltung**: Bilder löschen
- **Einstellungen**: Konfiguration ändern

### Webinterface

Öffnen Sie im Browser: `http://<raspberry-pi-ip>:8080`

**Features:**
- Bilder hochladen
- Bilder löschen
- Einstellungen ändern
- Systeminformationen anzeigen

## Email-Einrichtung

### Gmail

1. Zwei-Faktor-Authentifizierung aktivieren
2. App-Passwort erstellen:
   - Google-Konto → Sicherheit → App-Passwörter
   - Passwort für "Mail" generieren
3. In `config.yaml` eintragen:
   ```yaml
   email:
     imap_server: "imap.gmail.com"
     username: "ihre-email@gmail.com"
     password: "<app-passwort>"  # Nicht das normale Passwort!
   ```

### Andere Provider

- **Outlook/Hotmail**: `imap-mail.outlook.com`
- **Yahoo**: `imap.mail.yahoo.com`
- **Custom**: IMAP-Server-Adresse in `config.yaml` eintragen

## Verzeichnisstruktur

```
PictureFrameV3/
├── src/
│   ├── main.py              # Hauptanwendung
│   ├── main_ui.py           # PyQt5 UI
│   ├── web_interface.py     # Flask Webinterface
│   ├── config_manager.py    # Konfigurationsverwaltung
│   ├── image_processor.py   # Bildverarbeitung
│   ├── email_handler.py      # Email-Empfang
│   ├── slideshow.py         # Slideshow-Logik
│   └── templates/
│       └── index.html       # Webinterface HTML
├── images/
│   ├── originals/          # Original-Bilder
│   └── proxies/             # Optimierte Proxy-Bilder
├── config.yaml              # Konfigurationsdatei
├── requirements.txt         # Python-Abhängigkeiten
├── install.sh               # Installations-Script
├── setup_bootscreen.sh      # Bootscreen-Setup
└── pictureframe.service     # Systemd-Service
```

## Bildformate

Unterstützte Formate:
- JPEG/JPG
- PNG
- GIF
- BMP
- WebP

Bilder werden automatisch:
- Auf 1024x600 Pixel optimiert
- In JPEG konvertiert
- Seitenverhältnis beibehalten
- Hochwertig komprimiert

## Troubleshooting

### Anwendung startet nicht

```bash
# Logs prüfen
tail -f logs/pictureframe.log

# Service-Status prüfen
sudo systemctl status pictureframe

# Manuell starten für Debugging
python3 src/main.py
```

### Email funktioniert nicht

- IMAP-Server-Adresse prüfen
- Benutzername/Passwort überprüfen
- Bei Gmail: App-Passwort verwenden (nicht normales Passwort)
- Firewall-Einstellungen prüfen

### Touchscreen reagiert nicht

- Touchscreen-Treiber installieren (je nach Modell)
- X11-Konfiguration prüfen: `/etc/X11/xorg.conf.d/99-touchscreen.conf`
- `xinput list` ausführen, um Gerät zu finden
- Bei Raspberry Pi OS Lite: `setup_lite.sh` ausführen

### Display-Auflösung falsch

Für 1024x600 Pixel Display, fügen Sie in `/boot/config.txt` hinzu:

```
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0
```

Dann neu starten: `sudo reboot`

### Webinterface nicht erreichbar

- IP-Adresse prüfen: `hostname -I`
- Port 8080 freigeben: `sudo ufw allow 8080`
- Firewall-Einstellungen prüfen

## Entwicklung

### Abhängigkeiten installieren

```bash
pip3 install -r requirements.txt
```

### Tests ausführen

```bash
# UI testen
python3 src/main.py

# Webinterface testen
python3 -c "from src.web_interface import WebInterface; from src.config_manager import ConfigManager; from src.image_processor import ImageProcessor; w = WebInterface(ConfigManager(), ImageProcessor()); w.run()"
```

## Wartung

### Logs

Logs befinden sich in `logs/pictureframe.log`

### Backup

Wichtige Dateien:
- `config.yaml` - Konfiguration
- `images/originals/` - Original-Bilder
- `images/proxies/` - Proxy-Bilder (können neu generiert werden)

### Updates

```bash
cd PictureFrameV3
git pull
pip3 install -r requirements.txt --upgrade
sudo systemctl restart pictureframe
```

## Lizenz

Dieses Projekt ist für den privaten Gebrauch entwickelt worden.

## Support

Bei Problemen:
1. Logs prüfen: `logs/pictureframe.log`
2. Service-Status: `sudo systemctl status pictureframe`
3. Manueller Start für Debugging: `python3 src/main.py`

## Changelog

### Version 1.0.0 (Production)
- Vollständige UI mit PyQt5
- Email-Integration mit automatischer Bildverarbeitung
- Webinterface für Remote-Verwaltung
- Custom Bootscreen mit Logo
- Touch-Gesten (Swipe, Zoom, Pan)
- Bildoptimierung und Proxy-Generierung
- EXIF-Metadaten-Extraktion (Datum, GPS, Location)
- Zeitgesteuerte Display-Ein/Ausschaltung
- WLAN-Verwaltung über GUI und Webinterface
- Fade-Übergänge in der Slideshow
- Production-ready Logging und Fehlerbehandlung

