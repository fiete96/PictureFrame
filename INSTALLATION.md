# Detaillierte Installationsanleitung

## System-Auswahl

### Option 1: Raspberry Pi OS Desktop (Empfohlen für Einsteiger)

**Vorteile:**
- ✅ Alles bereits vorinstalliert (X11, Display-Manager, etc.)
- ✅ Einfache Installation
- ✅ Weniger Konfiguration nötig
- ✅ Gute Dokumentation verfügbar

**Nachteile:**
- ❌ Größere Image-Größe (~2.5GB)
- ❌ Mehr Ressourcen-Verbrauch (aber für Pi Zero 2W ausreichend)

**Installation:**
1. Raspberry Pi Imager herunterladen
2. "Raspberry Pi OS (32-bit)" mit Desktop wählen
3. Auf SD-Karte schreiben
4. SSH aktivieren (optional)
5. SD-Karte in Pi einstecken und starten

### Option 2: Raspberry Pi OS Lite (Für Fortgeschrittene)

**Vorteile:**
- ✅ Sehr klein (~400MB)
- ✅ Minimaler Ressourcen-Verbrauch
- ✅ Mehr Kontrolle über installierte Pakete

**Nachteile:**
- ❌ Zusätzliches Setup nötig
- ❌ X11 muss manuell installiert werden
- ❌ Mehr Konfiguration erforderlich

**Installation:**
1. Raspberry Pi Imager herunterladen
2. "Raspberry Pi OS Lite (32-bit)" wählen
3. Auf SD-Karte schreiben
4. SSH aktivieren (wichtig, da kein Desktop vorhanden)
5. SD-Karte in Pi einstecken und starten
6. Per SSH verbinden
7. `setup_lite.sh` ausführen:

```bash
cd ~/PictureFrameV3
chmod +x setup_lite.sh
./setup_lite.sh
```

## Touchscreen-Konfiguration

### Häufige Touchscreen-Modelle

#### Waveshare 1024x600 HDMI Touchscreen

```bash
# Treiber installieren
git clone https://github.com/waveshare/LCD-show.git
cd LCD-show
sudo ./LCD102-600-show
```

#### Andere Modelle

1. Hersteller-Dokumentation prüfen
2. Treiber installieren
3. X11-Konfiguration anpassen: `/etc/X11/xorg.conf.d/99-touchscreen.conf`

### Touchscreen testen

```bash
# Nach X11-Start
xinput list
xinput test <device-id>
```

## Display-Auflösung konfigurieren

Für 1024x600 Pixel Display:

```bash
sudo nano /boot/config.txt
```

Hinzufügen:

```
# 1024x600 Display-Konfiguration
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0
hdmi_drive=2
```

Speichern und neu starten:

```bash
sudo reboot
```

## Projekt-Installation

### 1. Projekt kopieren

**Via Git (wenn Repository vorhanden):**
```bash
cd ~
git clone <repository-url> PictureFrameV3
cd PictureFrameV3
```

**Via SCP (vom Entwicklungsrechner):**
```bash
# Auf Entwicklungsrechner:
scp -r PictureFrameV3 pi@<raspberry-pi-ip>:~/
```

**Via USB-Stick:**
```bash
# USB-Stick einstecken
sudo mkdir /mnt/usb
sudo mount /dev/sda1 /mnt/usb
cp -r /mnt/usb/PictureFrameV3 ~/
cd ~/PictureFrameV3
```

### 2. Installation ausführen

```bash
chmod +x install.sh
./install.sh
```

Das Script:
- Installiert System-Abhängigkeiten
- Installiert Python-Pakete
- Erstellt benötigte Verzeichnisse
- Fragt nach Systemd-Service-Installation
- Fragt nach Bootscreen-Setup

### 3. Konfiguration

```bash
nano config.yaml
```

Mindestens konfigurieren:
- Email-Einstellungen (IMAP-Server, Benutzername, Passwort)
- Slideshow-Intervall

### 4. Testen

```bash
# Manuell starten
python3 src/main.py
```

Sollte funktionieren:
- ✅ Vollbild-Slideshow
- ✅ Touch-Gesten (Swipe links/rechts)
- ✅ Webinterface auf Port 8080

### 5. Autostart einrichten

```bash
# Service installieren (falls noch nicht geschehen)
sudo cp pictureframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pictureframe.service
sudo systemctl start pictureframe.service
```

## Bootscreen (Optional)

```bash
chmod +x setup_bootscreen.sh
./setup_bootscreen.sh
```

Nach Installation neu starten, um Bootscreen zu sehen.

## Troubleshooting

### X11 startet nicht

```bash
# Logs prüfen
cat ~/.xsession-errors

# X11 manuell starten
startx
```

### Touchscreen funktioniert nicht

```bash
# Geräte auflisten
xinput list

# Touchscreen-Konfiguration prüfen
cat /etc/X11/xorg.conf.d/99-touchscreen.conf

# Eventuell Treiber neu installieren
```

### Display-Auflösung falsch

```bash
# Aktuelle Auflösung prüfen
xrandr

# Manuell setzen (temporär)
xrandr --output HDMI-1 --mode 1024x600
```

### Service startet nicht

```bash
# Status prüfen
sudo systemctl status pictureframe

# Logs anzeigen
sudo journalctl -u pictureframe -f

# Service neu starten
sudo systemctl restart pictureframe
```

## Empfehlung

**Für Einsteiger:** Raspberry Pi OS Desktop verwenden
**Für Fortgeschrittene:** Raspberry Pi OS Lite + `setup_lite.sh`

Beide Varianten funktionieren gleich gut, Desktop ist einfacher zu konfigurieren.



