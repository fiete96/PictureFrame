#!/bin/bash
# Setup-Script für Custom Bootscreen auf Raspberry Pi

echo "Picture Frame - Bootscreen Setup"
echo "================================="

# Prüfe ob auf Raspberry Pi
if [ ! -f /proc/device-tree/model ]; then
    echo "Warnung: Dieses Script sollte auf einem Raspberry Pi ausgeführt werden"
fi

# Installiere plymouth (Bootscreen-System)
echo "Installiere plymouth..."
sudo apt-get update
sudo apt-get install -y plymouth plymouth-themes

# Erstelle Custom Plymouth Theme
THEME_DIR="/usr/share/plymouth/themes/pictureframe"
sudo mkdir -p "$THEME_DIR"

# Erstelle Script-Datei
sudo tee "$THEME_DIR/pictureframe.script" > /dev/null << 'EOF'
# Picture Frame Bootscreen Script

# Bildschirmgröße
WIDTH = 1024;
HEIGHT = 600;

# Hintergrundfarbe (schwarz)
Window.SetBackgroundTopColor(0.00, 0.00, 0.00);
Window.SetBackgroundBottomColor(0.00, 0.00, 0.00);

# Lade Logo (falls vorhanden)
logo_image = Image("logo.png");
logo_sprite = Sprite(logo_image);
logo_sprite.SetX(Window.GetWidth() / 2 - logo_image.GetWidth() / 2);
logo_sprite.SetY(Window.GetHeight() / 2 - logo_image.GetHeight() / 2 - 50);

# Text
message_sprite = Sprite();
message_sprite.SetPosition(Window.GetWidth() / 2, Window.GetHeight() / 2 + 100, 10000);

fun message_callback(text) {
    my_image = Image.Text(text, 1, 1, 1);
    message_sprite.SetImage(my_image);
    message_sprite.SetX(Window.GetWidth() / 2 - my_image.GetWidth() / 2);
}

Plymouth.SetMessageFunction(message_callback);

# Progress Bar
progress_bar = Image("progress_dot.png");
progress_sprite = Sprite(progress_bar);
progress_sprite.SetX(Window.GetWidth() / 2 - progress_bar.GetWidth() / 2);
progress_sprite.SetY(Window.GetHeight() / 2 + 150);

fun progress_callback(duration, progress) {
    progress_sprite.SetOpacity(progress);
}
EOF

# Kopiere Logo (falls vorhanden im Projekt-Verzeichnis)
# Prüfe verschiedene mögliche Pfade
if [ -f "$HOME/PictureFrameV3/Logo PictureFrame.png" ]; then
    LOGO_SOURCE="$HOME/PictureFrameV3/Logo PictureFrame.png"
elif [ -f "/home/pi/PictureFrameV3/Logo PictureFrame.png" ]; then
    LOGO_SOURCE="/home/pi/PictureFrameV3/Logo PictureFrame.png"
else
    LOGO_SOURCE=""
fi
if [ -n "$LOGO_SOURCE" ] && [ -f "$LOGO_SOURCE" ]; then
    echo "Kopiere Logo nach Plymouth-Theme..."
    # Konvertiere und skaliere Logo für Bootscreen (max 400px Breite, behält Seitenverhältnis)
    if command -v convert &> /dev/null; then
        convert "$LOGO_SOURCE" -resize 400x400\> -background transparent -gravity center -extent 400x400 "$THEME_DIR/logo.png"
        echo "Logo erfolgreich kopiert und angepasst"
    else
        # Fallback: Direkt kopieren (falls ImageMagick nicht verfügbar)
        cp "$LOGO_SOURCE" "$THEME_DIR/logo.png"
        echo "Logo kopiert (ImageMagick nicht verfügbar - Logo könnte zu groß sein)"
        echo "Hinweis: Installiere ImageMagick für optimale Logo-Größe: sudo apt-get install imagemagick"
    fi
else
    echo "Warnung: Logo nicht gefunden unter $LOGO_SOURCE"
    echo "Erstelle einfaches Logo (falls ImageMagick verfügbar)..."
    if command -v convert &> /dev/null; then
        convert -size 200x200 xc:none -fill white -gravity center -pointsize 48 -annotate +0+0 "PF" "$THEME_DIR/logo.png" 2>/dev/null || \
        echo "Hinweis: Installiere ImageMagick für Logo-Erstellung: sudo apt-get install imagemagick"
    fi
fi

# Erstelle Progress Dot
if [ ! -f "$THEME_DIR/progress_dot.png" ]; then
    if command -v convert &> /dev/null; then
        convert -size 300x10 xc:transparent -fill white -draw "rectangle 0,0 300,10" "$THEME_DIR/progress_dot.png" 2>/dev/null
    fi
fi

# Erstelle .plymouth Datei
sudo tee "$THEME_DIR/pictureframe.plymouth" > /dev/null << EOF
[Plymouth Theme]
Name=Picture Frame
Description=Custom Bootscreen for Picture Frame
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/pictureframe
ScriptFile=/usr/share/plymouth/themes/pictureframe/pictureframe.script
EOF

# Aktiviere Theme
echo "Aktiviere Bootscreen-Theme..."
sudo plymouth-set-default-theme pictureframe

# Aktiviere Plymouth-Services
echo "Aktiviere Plymouth-Services..."
sudo systemctl enable plymouth-start.service 2>/dev/null || true
sudo systemctl enable plymouth-quit.service 2>/dev/null || true

# Konfiguriere Boot-Parameter für Plymouth
echo "Konfiguriere Boot-Parameter..."
CMDLINE_FILE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE_FILE" ]; then
    # Prüfe ob quiet splash bereits vorhanden sind
    if ! grep -q "quiet splash" "$CMDLINE_FILE"; then
        # Füge quiet splash hinzu (wenn nicht vorhanden)
        sudo sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE_FILE"
        echo "Boot-Parameter 'quiet splash' hinzugefügt"
    else
        echo "Boot-Parameter bereits vorhanden"
    fi
else
    echo "Warnung: $CMDLINE_FILE nicht gefunden"
fi

# Update initramfs
echo "Aktualisiere initramfs..."
sudo update-initramfs -u

echo ""
echo "Bootscreen-Setup abgeschlossen!"
echo "Bitte System neu starten, um den neuen Bootscreen zu sehen."
echo ""
echo "Hinweis: Für bessere Ergebnisse können Sie ein eigenes Logo.png"
echo "         nach $THEME_DIR kopieren (empfohlene Größe: 200x200px)"

