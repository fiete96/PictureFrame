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
    python3-tk \
    fonts-noto-color-emoji \
    network-manager \
    wireless-tools \
    x11-xserver-utils \
    xorg \
    xserver-xorg \
    xinit \
    x11-utils \
    unclutter

# Installiere Python-Abhängigkeiten
echo "Installiere Python-Abhängigkeiten..."
# Upgrade pip (überspringe Fehler wenn pip von Debian installiert wurde)
sudo pip3 install --break-system-packages --upgrade pip setuptools wheel 2>/dev/null || echo "pip-Upgrade übersprungen (bereits installiert)"
# Installiere Abhängigkeiten
sudo pip3 install --break-system-packages -r requirements.txt

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

# Konfiguriere X11 für 1024x600 Display
echo "Konfiguriere X11 für Display..."
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/10-display.conf > /dev/null << 'EOF'
Section "Monitor"
    Identifier "HDMI-1"
    Modeline "1024x600_60.00" 49.00 1024 1072 1168 1312 600 603 613 624 -hsync +vsync
    Option "PreferredMode" "1024x600_60.00"
EndSection

Section "Screen"
    Identifier "Screen0"
    Monitor "HDMI-1"
    SubSection "Display"
        Modes "1024x600_60.00"
    EndSubSection
EndSection
EOF

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

# Installiere Systemd-Services
echo "Installiere Systemd-Services..."
# X11-Service
sudo cp x11.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable x11.service
echo "  ✓ x11.service installiert"

# unclutter-Service (Mauszeiger ausblenden)
sudo cp unclutter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable unclutter.service
echo "  ✓ unclutter.service installiert"

# PictureFrame-Service
sudo cp pictureframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pictureframe.service
echo "  ✓ pictureframe.service installiert"

# Erstelle config.yaml falls nicht vorhanden
if [ ! -f config.yaml ]; then
    echo "Erstelle config.yaml..."
    cat > config.yaml << 'EOF'
web:
  port: 80
display:
  width: 1024
  height: 600
  fullscreen: true
EOF
    echo "  ✓ config.yaml erstellt"
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
echo "1. Bearbeiten Sie config.yaml und konfigurieren Sie Email-Einstellungen (optional)"
echo "2. Starten Sie die Services mit:"
echo "   sudo systemctl start x11"
echo "   sudo systemctl start unclutter"
echo "   sudo systemctl start pictureframe"
echo "3. Oder starten Sie alle Services mit:"
echo "   sudo reboot"
echo ""
echo "Das Web-Interface ist erreichbar unter: http://$(hostname -I | awk '{print $1}')"
echo ""
