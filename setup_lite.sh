#!/bin/bash
# Setup-Script für Raspberry Pi OS Lite
# Installiert X11 und alle benötigten Komponenten für PictureFrame V3

set -e

echo "PictureFrame V3 - Raspberry Pi OS Lite Setup"
echo "=============================================="
echo ""
echo "Dieses Script installiert X11 und alle benötigten Komponenten"
echo "für die GUI-Anwendung auf Raspberry Pi OS Lite."
echo ""

# Prüfe ob bereits Desktop installiert ist
if dpkg -l | grep -q "raspberrypi-ui-mods\|lxde"; then
    echo "Desktop-Environment bereits installiert. Setup nicht nötig."
    exit 0
fi

# Prüfe ob auf Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warnung: Dieses Script sollte auf einem Raspberry Pi ausgeführt werden"
    read -p "Fortfahren? (j/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        exit 1
    fi
fi

echo "Installiere X11 und Display-Manager..."
sudo apt-get update
sudo apt-get install -y \
    xorg \
    xserver-xorg \
    xinit \
    x11-xserver-utils \
    lightdm \
    openbox \
    unclutter \
    xdotool

echo ""
echo "Konfiguriere X11 für Touchscreen..."

# Erstelle X11-Konfiguration für Touchscreen
sudo mkdir -p /etc/X11/xorg.conf.d

# Basis-Konfiguration für Touchscreen (wird je nach Modell angepasst)
sudo tee /etc/X11/xorg.conf.d/99-touchscreen.conf > /dev/null << 'EOF'
# Touchscreen-Konfiguration
# Diese Datei muss je nach Touchscreen-Modell angepasst werden
Section "InputClass"
    Identifier "touchscreen"
    MatchIsTouchscreen "on"
    Driver "libinput"
    Option "Tapping" "on"
    Option "TappingDrag" "on"
    Option "DisableWhileTyping" "off"
EndSection
EOF

echo ""
echo "Konfiguriere Autologin für Benutzer 'pi'..."
sudo mkdir -p /etc/lightdm
sudo tee /etc/lightdm/lightdm.conf > /dev/null << 'EOF'
[Seat:*]
autologin-user=pi
autologin-user-timeout=0
user-session=openbox
EOF

echo ""
echo "Erstelle .xprofile für Autostart..."
mkdir -p ~/.config/openbox

# .xprofile für Autostart der Anwendung
cat > ~/.xprofile << 'EOF'
#!/bin/bash
# Autostart für PictureFrame V3

# Warte bis Display bereit ist
sleep 2

# Starte PictureFrame V3
cd ~/PictureFrameV3
python3 src/main.py &
EOF

chmod +x ~/.xprofile

# Openbox Autostart
cat > ~/.config/openbox/autostart << 'EOF'
# Autostart-Script für Openbox

# Cursor verstecken nach 3 Sekunden Inaktivität
unclutter -idle 3 -root &

# Starte PictureFrame V3
cd ~/PictureFrameV3
python3 src/main.py &
EOF

chmod +x ~/.config/openbox/autostart

echo ""
echo "Konfiguriere Display-Auflösung für 1024x600..."
# Erstelle xorg.conf für Display-Auflösung
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

echo ""
echo "Setup abgeschlossen!"
echo ""
echo "WICHTIG: Bitte konfigurieren Sie:"
echo "1. Touchscreen-Treiber (je nach Modell)"
echo "2. Display-Auflösung in /boot/config.txt falls nötig"
echo ""
echo "Beispiel für /boot/config.txt:"
echo "  hdmi_group=2"
echo "  hdmi_mode=87"
echo "  hdmi_cvt=1024 600 60 6 0 0 0"
echo ""
echo "Nach der Konfiguration:"
echo "  sudo reboot"
echo ""
read -p "Möchten Sie jetzt neu starten? (j/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Jj]$ ]]; then
    sudo reboot
fi



