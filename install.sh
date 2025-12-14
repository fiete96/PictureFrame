#!/bin/bash
# Installations-Script für PictureFrame V3

set -e

echo "PictureFrame V3 - Installation"
echo "================================"

# Prüfe ob Python 3 installiert ist
if ! command -v python3 &> /dev/null; then
    echo "Fehler: Python 3 ist nicht installiert"
    exit 1
fi

# Installiere System-Abhängigkeiten
echo "Installiere System-Abhängigkeiten..."
sudo apt-get update
sudo apt-get install -y \
    git \
    python3-pip \
    python3-pyqt5 \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    libfreetype6-dev \
    liblcms2-dev \
    libopenjp2-7-dev \
    libtiff5-dev \
    tcl8.6-dev \
    tk8.6-dev \
    python3-tk

# Installiere Python-Abhängigkeiten
echo "Installiere Python-Abhängigkeiten..."
# Upgrade pip zuerst
pip3 install --upgrade pip setuptools wheel
# Installiere Abhängigkeiten
pip3 install -r requirements.txt

# Erstelle benötigte Verzeichnisse
echo "Erstelle Verzeichnisse..."
mkdir -p images/originals
mkdir -p images/proxies
mkdir -p temp
mkdir -p logs

# Setze Berechtigungen
echo "Setze Berechtigungen..."
chmod +x src/main.py
chmod +x setup_bootscreen.sh

# Git-Repository einrichten (falls noch nicht vorhanden)
echo "Richte Git-Repository ein..."
if [ ! -d .git ]; then
    git init
    git remote add origin https://github.com/fiete96/PictureFrame.git 2>/dev/null || echo "Remote bereits vorhanden oder Fehler"
    git fetch origin main 2>/dev/null || echo "Fetch fehlgeschlagen (normal wenn Repository leer ist)"
    git branch -M main 2>/dev/null
    git branch --set-upstream-to=origin/main main 2>/dev/null || echo "Upstream-Branch konnte nicht gesetzt werden"
    echo "Git-Repository initialisiert"
else
    echo "Git-Repository bereits vorhanden"
    git remote set-url origin https://github.com/fiete96/PictureFrame.git 2>/dev/null || echo "Remote konnte nicht gesetzt werden"
fi

# Installiere Systemd-Service (optional)
read -p "Möchten Sie den Systemd-Service installieren? (j/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Jj]$ ]]; then
    echo "Installiere Systemd-Service..."
    sudo cp pictureframe.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable pictureframe.service
    echo "Service installiert. Starten mit: sudo systemctl start pictureframe"
fi

# Bootscreen-Setup (optional)
read -p "Möchten Sie den Custom Bootscreen einrichten? (j/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Jj]$ ]]; then
    echo "Richte Bootscreen ein..."
    chmod +x setup_bootscreen.sh
    ./setup_bootscreen.sh
fi

echo ""
echo "Installation abgeschlossen!"
echo ""
echo "Nächste Schritte:"
echo "1. Bearbeiten Sie config.yaml und konfigurieren Sie Email-Einstellungen"
echo "2. Starten Sie die Anwendung mit: python3 src/main.py"
echo "3. Öffnen Sie http://localhost:8080 im Browser für das Webinterface"
echo ""
echo "Für Autostart: sudo systemctl start pictureframe"

