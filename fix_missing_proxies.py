#!/usr/bin/env python3
"""
Skript zum Nachträglichen Erstellen fehlender Proxy-Bilder
Findet Original-Bilder ohne entsprechende Proxy-Bilder und erstellt diese
"""
import sys
from pathlib import Path

# Füge src-Verzeichnis zum Python-Pfad hinzu
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from image_processor import ImageProcessor
from exif_extractor import ExifExtractor
from playlist_manager import PlaylistManager
import logging
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    """Hauptfunktion"""
    # Pfade
    base_dir = Path(__file__).parent
    original_dir = base_dir / 'images' / 'originals'
    proxy_dir = base_dir / 'images' / 'proxies'
    metadata_file = proxy_dir / 'metadata.json'
    
    # Stelle sicher, dass Verzeichnisse existieren
    original_dir.mkdir(parents=True, exist_ok=True)
    proxy_dir.mkdir(parents=True, exist_ok=True)
    
    # Initialisiere Komponenten
    processor = ImageProcessor()
    playlist_manager = PlaylistManager(proxy_dir, metadata_file)
    
    # Lade bestehende Metadaten
    metadata = {}
    if metadata_file.exists():
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Fehler beim Laden der Metadaten: {e}")
            metadata = {}
    
    # Finde fehlende Proxy-Bilder
    missing_proxies = []
    processed_count = 0
    error_count = 0
    
    logger.info(f"Suche nach fehlenden Proxy-Bildern in {original_dir}...")
    
    for orig_file in original_dir.rglob('*'):
        if not orig_file.is_file():
            continue
        
        # Prüfe ob Dateiformat unterstützt wird
        if not processor.is_supported(orig_file):
            continue
        
        try:
            # Berechne Hash
            file_hash = processor._get_file_hash(orig_file)
            proxy_path = proxy_dir / f"{file_hash}.jpg"
            
            # Prüfe ob Proxy fehlt
            if not proxy_path.exists():
                missing_proxies.append((orig_file, file_hash, proxy_path))
        except Exception as e:
            logger.warning(f"Fehler beim Prüfen von {orig_file.name}: {e}")
            error_count += 1
    
    logger.info(f"Gefunden: {len(missing_proxies)} fehlende Proxy-Bilder")
    
    if not missing_proxies:
        logger.info("Alle Proxy-Bilder vorhanden!")
        return
    
    # Erstelle fehlende Proxy-Bilder
    logger.info("Erstelle fehlende Proxy-Bilder...")
    
    for orig_file, file_hash, proxy_path in missing_proxies:
        try:
            logger.info(f"Verarbeite: {orig_file.name} -> {proxy_path.name}")
            
            # Erstelle Proxy
            created_proxy = processor.process_image(orig_file, proxy_dir)
            
            # Extrahiere EXIF-Daten
            exif_data = ExifExtractor.extract_all_exif(orig_file)
            
            # Aktualisiere Metadaten (falls noch nicht vorhanden)
            if file_hash not in metadata:
                metadata[file_hash] = {
                    'sender': 'Nachträgliche Verarbeitung',
                    'subject': '',
                    'date': exif_data.get('date') or datetime.now().isoformat(),
                    'location': exif_data.get('location'),
                    'latitude': exif_data.get('latitude'),
                    'longitude': exif_data.get('longitude'),
                    'exif_data': exif_data
                }
            
            # Füge zu Playlists hinzu
            playlist_manager.add_image(file_hash)
            
            processed_count += 1
            
            # Speicherfreigabe
            import gc
            gc.collect()
            
        except Exception as e:
            logger.error(f"Fehler beim Verarbeiten von {orig_file.name}: {e}", exc_info=True)
            error_count += 1
    
    # Speichere Metadaten
    try:
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        logger.info(f"Metadaten gespeichert")
    except Exception as e:
        logger.error(f"Fehler beim Speichern der Metadaten: {e}")
    
    logger.info(f"Fertig! Verarbeitet: {processed_count}, Fehler: {error_count}")

if __name__ == '__main__':
    main()


