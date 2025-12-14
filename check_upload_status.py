#!/usr/bin/env python3
"""
Prüft den aktuellen Status der Upload-Verarbeitung
"""
import sys
import requests
from pathlib import Path

# Pfad für Imports hinzufügen
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def check_status():
    """Prüft den Upload-Status über die Web-API"""
    try:
        # Versuche über Web-API (falls Server läuft)
        try:
            response = requests.get('http://localhost:5000/api/upload/status', timeout=2)
            if response.status_code == 200:
                status = response.json()
                print('=== Upload-Verarbeitungs-Status (via Web-API) ===')
                print(f'Verarbeitung läuft: {status["is_processing"]}')
                print(f'Upload läuft: {status["upload_in_progress"]}')
                print(f'Bilder in Queue: {status["queue_size"]}')
                print(f'Timer aktiv: {status["timer_active"]}')
                print(f'Verzögerung: {status["processing_delay"]} Sekunden')
                print(f'Batch-Größe: {status["batch_size"]} Bilder')
                
                if status['is_processing']:
                    print('\n✓ Verarbeitung läuft gerade!')
                elif status['queue_size'] > 0:
                    if status['timer_active']:
                        print(f'\n⏳ {status["queue_size"]} Bilder warten auf Verarbeitung (Timer läuft)')
                    else:
                        print(f'\n⚠️  {status["queue_size"]} Bilder in Queue, aber kein Timer aktiv!')
                elif status['upload_in_progress']:
                    print('\n📤 Upload läuft gerade')
                else:
                    print('\n✓ Keine Verarbeitung aktiv, Queue ist leer')
                return
        except (requests.exceptions.RequestException, ConnectionError):
            pass  # Server läuft nicht, versuche direkten Zugriff
        
        # Direkter Zugriff (falls Server nicht läuft)
        from web_interface import WebInterface
        from config_manager import ConfigManager
        from image_processor import ImageProcessor
        
        config = ConfigManager()
        processor = ImageProcessor()
        web = WebInterface(config, processor)
        
        with web.app.test_client() as client:
            response = client.get('/api/upload/status')
            if response.status_code == 200:
                status = response.get_json()
                print('=== Upload-Verarbeitungs-Status (direkt) ===')
                print(f'Verarbeitung läuft: {status["is_processing"]}')
                print(f'Upload läuft: {status["upload_in_progress"]}')
                print(f'Bilder in Queue: {status["queue_size"]}')
                print(f'Timer aktiv: {status["timer_active"]}')
                print(f'Verzögerung: {status["processing_delay"]} Sekunden')
                print(f'Batch-Größe: {status["batch_size"]} Bilder')
                
                if status['is_processing']:
                    print('\n✓ Verarbeitung läuft gerade!')
                elif status['queue_size'] > 0:
                    if status['timer_active']:
                        print(f'\n⏳ {status["queue_size"]} Bilder warten auf Verarbeitung (Timer läuft)')
                    else:
                        print(f'\n⚠️  {status["queue_size"]} Bilder in Queue, aber kein Timer aktiv!')
                elif status['upload_in_progress']:
                    print('\n📤 Upload läuft gerade')
                else:
                    print('\n✓ Keine Verarbeitung aktiv, Queue ist leer')
            else:
                print(f'Fehler beim Abrufen des Status: {response.status_code}')
    except Exception as e:
        print(f'Fehler: {e}')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_status()

