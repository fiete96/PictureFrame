#!/bin/bash
# Fix-Script für fehlende Python-Abhängigkeiten

set -e

echo "PictureFrame V3 - Dependency Fix"
echo "================================="

# Prüfe ob pip3 installiert ist
if ! command -v pip3 &> /dev/null; then
    echo "Installiere pip3..."
    sudo apt-get update
    sudo apt-get install -y python3-pip
fi

# Upgrade pip
echo "Upgrade pip..."
pip3 install --upgrade pip

# Installiere alle Python-Abhängigkeiten
echo "Installiere Python-Abhängigkeiten..."
pip3 install -r requirements.txt

# Prüfe Installation
echo ""
echo "Prüfe Installation..."
python3 -c "import PIL; print('✓ Pillow installiert')" || echo "✗ Pillow fehlt"
python3 -c "import PyQt5; print('✓ PyQt5 installiert')" || echo "✗ PyQt5 fehlt"
python3 -c "import flask; print('✓ Flask installiert')" || echo "✗ Flask fehlt"
python3 -c "import imapclient; print('✓ imapclient installiert')" || echo "✗ imapclient fehlt"
python3 -c "import yaml; print('✓ PyYAML installiert')" || echo "✗ PyYAML fehlt"

echo ""
echo "Fertig! Versuchen Sie jetzt: python3 src/main.py"



