"""
Webinterface für Picture Frame
Ermöglicht Remote-Verwaltung über Browser
"""
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, send_from_directory
from pathlib import Path
import logging
import shutil
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime
from functools import lru_cache
from PIL import Image
import io

from config_manager import ConfigManager
from image_processor import ImageProcessor
from exif_extractor import ExifExtractor
from playlist_manager import PlaylistManager

logger = logging.getLogger(__name__)

class WebInterface:
    def __init__(self, config: ConfigManager, image_processor: ImageProcessor, settings_queue=None):
        self.config = config
        self.image_processor = image_processor
        self.settings_queue = settings_queue  # Queue für Thread-sichere Kommunikation
        self.app = Flask(__name__)
        self.app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
        
        # Production: Unterdrücke Werkzeug-Warnungen
        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.setLevel(logging.WARNING)
        
        # Cache für Bildliste (wird bei Änderungen invalidiert)
        self._images_cache = None
        self._images_cache_time = None
        
        # Thumbnail-Verzeichnis
        self.thumbnail_dir = Path(self.config.get('paths.proxy_images')) / 'thumbnails'
        self.thumbnail_dir.mkdir(exist_ok=True)
        
        self.setup_routes()
    
    def setup_routes(self):
        """Richtet alle Web-Routen ein"""
        
        @self.app.route('/')
        def index():
            """Hauptseite"""
            return render_template('index.html')
        
        @self.app.route('/api/logo')
        def get_logo():
            """Gibt das Logo zurück"""
            try:
                logo_path = Path(__file__).parent.parent / "Logo PictureFrame.png"
                if logo_path.exists():
                    return send_file(str(logo_path), mimetype='image/png')
                else:
                    # Fallback: 404
                    return "Logo nicht gefunden", 404
            except Exception as e:
                logger.error(f"Fehler beim Laden des Logos: {e}")
                return "Fehler beim Laden des Logos", 500
        
        def _get_favicon():
            """Hilfsfunktion zum Laden des Favicons (konvertiert PNG zu ICO falls nötig)"""
            try:
                favicon_path = Path(__file__).parent.parent / "Logo PictureFrame Kopie.png"
                if not favicon_path.exists():
                    # Fallback: normales Logo
                    favicon_path = Path(__file__).parent.parent / "Logo PictureFrame.png"
                
                if not favicon_path.exists():
                    return "Favicon nicht gefunden", 404
                
                # Prüfe ob ICO-Version existiert, sonst erstelle sie
                ico_path = favicon_path.parent / "favicon.ico"
                
                if not ico_path.exists():
                    # Konvertiere PNG zu ICO mit Pillow
                    try:
                        with Image.open(favicon_path) as img:
                            # Resize auf Standard-Favicon-Größen (16x16, 32x32, 48x48)
                            # ICO kann mehrere Größen enthalten
                            sizes = [(16, 16), (32, 32), (48, 48)]
                            ico_images = []
                            for size in sizes:
                                resized = img.resize(size, Image.Resampling.LANCZOS)
                                ico_images.append(resized)
                            
                            # Speichere als ICO (Pillow unterstützt mehrere Größen)
                            ico_images[0].save(str(ico_path), format='ICO', sizes=[(img.width, img.height) for img in ico_images])
                            logger.info(f"Favicon ICO erstellt: {ico_path}")
                    except Exception as e:
                        logger.warning(f"Konnte ICO nicht erstellen, verwende PNG: {e}")
                        # Fallback: PNG senden
                        return send_file(str(favicon_path), mimetype='image/png')
                
                # ICO senden
                return send_file(str(ico_path), mimetype='image/x-icon')
            except Exception as e:
                logger.error(f"Fehler beim Laden des Favicons: {e}")
                return "Fehler beim Laden des Favicons", 500
        
        @self.app.route('/api/favicon')
        def get_favicon():
            """Gibt das Favicon zurück"""
            return _get_favicon()
        
        @self.app.route('/favicon.ico')
        def get_favicon_ico():
            """Gibt das Favicon zurück (Standard-Pfad für Browser)"""
            return _get_favicon()
        
        @self.app.route('/api/images')
        def get_images():
            """Gibt Liste aller Bilder zurück (optimiert mit Cache)"""
            # Cache prüfen (5 Sekunden Gültigkeit)
            import time
            current_time = time.time()
            if self._images_cache is not None and self._images_cache_time and (current_time - self._images_cache_time) < 5:
                return jsonify(self._images_cache)
            
            proxy_dir = Path(self.config.get('paths.proxy_images'))
            original_dir = Path(self.config.get('paths.original_images'))
            
            # Erstelle Hash-Map für Original-Dateien (einmalig)
            hash_to_original = {}
            if original_dir.exists():
                for orig_file in original_dir.rglob("*"):
                    if orig_file.is_file():
                        try:
                            file_hash = self.image_processor._get_file_hash(orig_file)
                            hash_to_original[file_hash] = {
                                'path': orig_file,
                                'name': orig_file.name
                            }
                        except Exception as e:
                            # Fehler beim Hash-Berechnen
                            continue
            
            # Bilder-Liste erstellen (sortiert nach Upload-Datum = Modifikationszeit)
            images = []
            proxy_files = list(proxy_dir.glob("*.jpg"))
            # Sortiere nach Modifikationszeit (neueste zuerst)
            proxy_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
            
            for proxy_file in proxy_files:
                proxy_hash = proxy_file.stem
                original_info = hash_to_original.get(proxy_hash, None)
                
                images.append({
                    'proxy': proxy_file.name,
                    'original': original_info['name'] if original_info else proxy_file.name,
                    'proxy_path': str(proxy_file),
                    'original_path': str(original_info['path']) if original_info else None
                })
            
            # Cache aktualisieren
            self._images_cache = images
            self._images_cache_time = current_time
            
            return jsonify(images)
        
        @self.app.route('/api/images/<filename>')
        def get_image(filename):
            """Gibt ein Bild zurück (mit Thumbnail-Option)"""
            try:
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                # Absoluten Pfad erstellen, falls relativ
                if not proxy_dir.is_absolute():
                    proxy_dir = Path.cwd() / proxy_dir
                
                file_path = proxy_dir / secure_filename(filename)
                
                # Sicherheitsprüfung: Datei muss im proxy_dir sein
                try:
                    file_path.resolve().relative_to(proxy_dir.resolve())
                except ValueError:
                    return "Ungültiger Pfad", 403
                
                if not file_path.exists() or file_path.suffix.lower() != '.jpg':
                    return "Bild nicht gefunden", 404
                
                # Prüfe ob Thumbnail angefordert wird
                thumbnail = request.args.get('thumbnail', 'false').lower() == 'true'
                
                if thumbnail:
                    # Thumbnail generieren oder aus Cache laden
                    thumbnail_path = self.thumbnail_dir / file_path.name
                    
                    if not thumbnail_path.exists():
                        # Thumbnail generieren
                        try:
                            with Image.open(file_path) as img:
                                # Thumbnail erstellen (max 300x300, Aspect Ratio beibehalten)
                                img.thumbnail((300, 300), Image.Resampling.LANCZOS)
                                
                                # In BytesIO speichern
                                thumb_io = io.BytesIO()
                                img.save(thumb_io, format='JPEG', quality=75, optimize=True)
                                thumb_io.seek(0)
                                
                                # Thumbnail speichern
                                thumbnail_path.parent.mkdir(exist_ok=True)
                                with open(thumbnail_path, 'wb') as f:
                                    f.write(thumb_io.getvalue())
                        except Exception as e:
                            logger.error(f"Fehler beim Generieren des Thumbnails: {e}")
                            # Fallback: Original senden
                            return send_file(str(file_path.resolve()), mimetype='image/jpeg')
                    
                    return send_file(str(thumbnail_path.resolve()), mimetype='image/jpeg')
                else:
                    # Original senden
                    return send_file(str(file_path.resolve()), mimetype='image/jpeg')
            except Exception as e:
                logger.error(f"Fehler beim Laden des Bildes {filename}: {e}")
                return f"Fehler: {str(e)}", 500
        
        @self.app.route('/api/images/<filename>', methods=['DELETE'])
        def delete_image(filename):
            """Löscht ein Bild und invalidiert den Cache"""
            """Löscht ein Bild (Proxy und Original)"""
            try:
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                original_dir = Path(self.config.get('paths.original_images'))
                
                proxy_file = proxy_dir / secure_filename(filename)
                if not proxy_file.exists():
                    return jsonify({'success': False, 'error': 'Proxy-Bild nicht gefunden'}), 404
                
                # Hash des Proxy-Bildes (Dateiname ohne Extension ist der Hash)
                proxy_hash = proxy_file.stem
                
                # Proxy-Datei löschen
                proxy_file.unlink()
                logger.info(f"Proxy-Datei gelöscht: {proxy_file.name}")
                
                # Original-Datei finden und löschen
                original_found = False
                for orig_file in original_dir.rglob("*"):
                    if orig_file.is_file():
                        # Prüfe ob Hash übereinstimmt
                        orig_hash = self.image_processor._get_file_hash(orig_file)
                        if orig_hash == proxy_hash:
                            orig_file.unlink()
                            logger.info(f"Original-Datei gelöscht: {orig_file.name}")
                            original_found = True
                            break
                
                if not original_found:
                    logger.warning(f"Original-Datei für {proxy_file.name} nicht gefunden (Hash: {proxy_hash})")
                
                # Metadaten löschen
                metadata_file = proxy_dir / 'metadata.json'
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        if proxy_hash in metadata:
                            del metadata[proxy_hash]
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2, ensure_ascii=False)
                            logger.info(f"Metadaten für {proxy_file.name} gelöscht")
                    except Exception as e:
                        logger.error(f"Fehler beim Löschen der Metadaten: {e}")
                
                # Playlists aktualisieren
                try:
                    playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                    playlist_manager.remove_image(proxy_hash)
                except Exception as e:
                    logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                
                # Cache invalidieren
                self._images_cache = None
                self._images_cache_time = None
                
                # Thumbnail auch löschen falls vorhanden
                thumbnail_path = self.thumbnail_dir / secure_filename(filename)
                if thumbnail_path.exists():
                    try:
                        thumbnail_path.unlink()
                    except Exception:
                        # Thumbnail konnte nicht gelöscht werden
                        pass
                
                return jsonify({'success': True, 'proxy_deleted': True, 'original_deleted': original_found})
            except Exception as e:
                logger.error(f"Fehler beim Löschen: {e}", exc_info=True)
                return jsonify({'success': False, 'error': str(e)}), 500
        
        @self.app.route('/api/images/<filename>/download')
        def download_original(filename):
            """Lädt die Original-Datei herunter"""
            try:
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                original_dir = Path(self.config.get('paths.original_images'))
                
                proxy_file = proxy_dir / secure_filename(filename)
                if not proxy_file.exists():
                    return "Proxy-Bild nicht gefunden", 404
                
                # Hash des Proxy-Bildes (Dateiname ohne Extension ist der Hash)
                proxy_hash = proxy_file.stem
                
                # Original-Datei finden
                original_file = None
                for orig_file in original_dir.rglob("*"):
                    if orig_file.is_file():
                        orig_hash = self.image_processor._get_file_hash(orig_file)
                        if orig_hash == proxy_hash:
                            original_file = orig_file
                            break
                
                if not original_file or not original_file.exists():
                    return "Original-Datei nicht gefunden", 404
                
                # Original-Datei zurückgeben
                return send_file(
                    str(original_file.resolve()),
                    as_attachment=True,
                    download_name=original_file.name,
                    mimetype='application/octet-stream'
                )
            except Exception as e:
                logger.error(f"Fehler beim Download: {e}", exc_info=True)
                return f"Fehler: {str(e)}", 500
        
        @self.app.route('/api/upload', methods=['POST'])
        def upload_image():
            """Lädt ein Bild hoch"""
            if 'file' not in request.files:
                return jsonify({'success': False, 'error': 'Keine Datei'}), 400
            
            file = request.files['file']
            if file.filename == '':
                return jsonify({'success': False, 'error': 'Keine Datei ausgewählt'}), 400
            
            if file and self.image_processor.is_supported(Path(file.filename)):
                try:
                    # Original speichern
                    original_dir = Path(self.config.get('paths.original_images'))
                    filename = secure_filename(file.filename)
                    original_path = original_dir / filename
                    
                    # Falls Datei existiert, Nummer anhängen
                    counter = 1
                    while original_path.exists():
                        stem = Path(filename).stem
                        suffix = Path(filename).suffix
                        original_path = original_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                    
                    file.save(str(original_path))
                    
                    # Proxy erstellen
                    proxy_dir = Path(self.config.get('paths.proxy_images'))
                    proxy_path = self.image_processor.process_image(original_path, proxy_dir)
                    
                    # EXIF-Daten extrahieren und speichern
                    exif_data = ExifExtractor.extract_all_exif(original_path)
                    
                    # Metadaten speichern (Manueller Upload)
                    metadata_file = proxy_dir / 'metadata.json'
                    metadata = {}
                    if metadata_file.exists():
                        try:
                            with open(metadata_file, 'r', encoding='utf-8') as f:
                                metadata = json.load(f)
                        except:
                            metadata = {}
                    
                    # Name aus POST-Request holen (falls vorhanden)
                    uploader_name = request.form.get('name', 'Manueller Upload')
                    if not uploader_name or uploader_name.strip() == '':
                        uploader_name = 'Manueller Upload'
                    
                    image_hash = proxy_path.stem
                    # Metadaten speichern - nur einmal, ohne Duplikation
                    # Die Top-Level-Felder werden aus exif_data übernommen für schnellen Zugriff
                    metadata[image_hash] = {
                        'sender': uploader_name.strip(),
                        'subject': '',
                        'date': exif_data.get('date') or datetime.now().isoformat(),  # EXIF-Datum oder aktuelles Datum
                        'location': exif_data.get('location'),  # Stadt (Land) - für schnellen Zugriff
                        'latitude': exif_data.get('latitude'),  # Für schnellen Zugriff
                        'longitude': exif_data.get('longitude'),  # Für schnellen Zugriff
                        'exif_data': exif_data  # Vollständige EXIF-Daten für Sortierung und zukünftige Erweiterungen
                    }
                    # Hinweis: Die Top-Level-Felder (date, location, latitude, longitude) sind Duplikate von exif_data
                    # für schnellen Zugriff ohne in exif_data zu graben. exif_data enthält die vollständigen EXIF-Daten.
                    
                    metadata_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(metadata_file, 'w', encoding='utf-8') as f:
                        json.dump(metadata, f, indent=2, ensure_ascii=False)
                    
                    # Playlists aktualisieren (optimiert für Bulk-Uploads)
                    # Nur zu transfer_time Playlist hinzufügen (schnell)
                    # Andere Playlists werden beim nächsten Slideshow-Refresh neu aufgebaut
                    try:
                        playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                        playlist_manager._add_to_playlist(image_hash, "transfer_time")
                    except Exception as e:
                        logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                    
                    # Cache invalidieren nach Upload
                    self._images_cache = None
                    self._images_cache_time = None
                    
                    # Thumbnail für neues Bild generieren
                    try:
                        thumbnail_path = self.thumbnail_dir / proxy_path.name
                        if not thumbnail_path.exists():
                            with Image.open(proxy_path) as img:
                                img.thumbnail((300, 300), Image.Resampling.LANCZOS)
                                thumb_io = io.BytesIO()
                                img.save(thumb_io, format='JPEG', quality=75, optimize=True)
                                thumb_io.seek(0)
                                thumbnail_path.parent.mkdir(exist_ok=True)
                                with open(thumbnail_path, 'wb') as f:
                                    f.write(thumb_io.getvalue())
                    except Exception:
                        # Thumbnail konnte nicht generiert werden
                        pass
                    
                    return jsonify({'success': True, 'message': 'Bild erfolgreich hochgeladen'})
                except Exception as e:
                    logger.error(f"Fehler beim Hochladen: {e}")
                    return jsonify({'success': False, 'error': str(e)}), 500
            
            return jsonify({'success': False, 'error': 'Ungültiges Dateiformat'}), 400
        
        @self.app.route('/api/config')
        def get_config():
            """Gibt die aktuelle Konfiguration zurück"""
            return jsonify(self.config.get_all())
        
        @self.app.route('/api/config', methods=['POST'])
        def update_config():
            """Aktualisiert die Konfiguration"""
            try:
                data = request.json
                
                # Validierte Updates
                if 'slideshow' in data:
                    if 'auto_play' in data['slideshow']:
                        self.config.set('slideshow.auto_play', bool(data['slideshow']['auto_play']))
                    if 'interval_seconds' in data['slideshow']:
                        self.config.set('slideshow.interval_seconds', int(data['slideshow']['interval_seconds']))
                    if 'transition_duration' in data['slideshow']:
                        self.config.set('slideshow.transition_duration', float(data['slideshow']['transition_duration']))
                    if 'sort_by' in data['slideshow']:
                        sort_by = str(data['slideshow']['sort_by'])
                        if sort_by in ['transfer_time', 'creation_time', 'random']:
                            self.config.set('slideshow.sort_by', sort_by)
                            # Für Kompatibilität: shuffle entsprechend setzen
                            self.config.set('slideshow.shuffle', (sort_by == "random"))
                    if 'shuffle' in data['slideshow']:
                        # Deprecated: shuffle wird durch sort_by ersetzt
                        shuffle_enabled = bool(data['slideshow']['shuffle'])
                        self.config.set('slideshow.shuffle', shuffle_enabled)
                        # Wenn sort_by nicht gesetzt ist, setze es basierend auf shuffle
                        if 'sort_by' not in data['slideshow']:
                            self.config.set('slideshow.sort_by', "random" if shuffle_enabled else "transfer_time")
                    if 'loop' in data['slideshow']:
                        self.config.set('slideshow.loop', bool(data['slideshow']['loop']))
                
                if 'email' in data:
                    if 'imap_server' in data['email']:
                        self.config.set('email.imap_server', data['email']['imap_server'])
                    if 'username' in data['email']:
                        self.config.set('email.username', data['email']['username'])
                    if 'password' in data['email']:
                        self.config.set('email.password', data['email']['password'])
                    if 'check_interval_minutes' in data['email']:
                        self.config.set('email.check_interval_minutes', int(data['email']['check_interval_minutes']))
                    if 'auto_reply' in data['email']:
                        self.config.set('email.auto_reply', bool(data['email']['auto_reply']))
                    if 'reply_message' in data['email']:
                        self.config.set('email.reply_message', data['email']['reply_message'])
                
                if 'display' in data:
                    if 'dpms_enabled' in data['display']:
                        self.config.set('display.dpms_enabled', bool(data['display']['dpms_enabled']))
                    if 'dpms_standby_minutes' in data['display']:
                        self.config.set('display.dpms_standby_minutes', int(data['display']['dpms_standby_minutes']))
                    if 'schedule_enabled' in data['display']:
                        self.config.set('display.schedule_enabled', bool(data['display']['schedule_enabled']))
                    if 'schedule_on_time' in data['display']:
                        self.config.set('display.schedule_on_time', str(data['display']['schedule_on_time']))
                    if 'schedule_off_time' in data['display']:
                        self.config.set('display.schedule_off_time', str(data['display']['schedule_off_time']))
                
                if 'wifi' in data:
                    if 'ssid' in data['wifi']:
                        self.config.set('wifi.ssid', data['wifi']['ssid'])
                    if 'password' in data['wifi']:
                        self.config.set('wifi.password', data['wifi']['password'])
                
                # Signalisiere, dass Einstellungen aktualisiert wurden (über Queue)
                if self.settings_queue:
                    try:
                        self.settings_queue.put('reload_settings', block=False)
                        logger.info("Einstellungen über Webinterface aktualisiert - Reload-Signal gesendet")
                    except queue.Full:
                        logger.warning("Settings-Queue voll, überspringe Signal")
                    except Exception as e:
                        logger.error(f"Fehler beim Senden des Reload-Signals: {e}")
                
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Fehler beim Aktualisieren der Konfiguration: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500
        
        @self.app.route('/api/system/info')
        def system_info():
            """Gibt Systeminformationen zurück"""
            proxy_dir = Path(self.config.get('paths.proxy_images'))
            original_dir = Path(self.config.get('paths.original_images'))
            
            proxy_count = len(list(proxy_dir.glob("*.jpg"))) if proxy_dir.exists() else 0
            original_count = len([f for f in original_dir.rglob("*") if f.is_file()]) if original_dir.exists() else 0
            
            return jsonify({
                'proxy_images': proxy_count,
                'original_images': original_count,
                'config': self.config.get_all()
            })
        
        @self.app.route('/api/email/test', methods=['POST'])
        def test_email():
            """Testet die Email-Verbindung"""
            try:
                data = request.get_json()
                if not data:
                    return jsonify({'success': False, 'error': 'Keine Daten übergeben'}), 400
                
                imap_server = data.get('imap_server', '')
                username = data.get('username', '')
                password = data.get('password', '')
                
                if not imap_server or not username or not password:
                    return jsonify({'success': False, 'error': 'IMAP Server, Benutzername und Passwort sind erforderlich'}), 400
                
                # Teste Email-Verbindung
                from email_handler import EmailHandler
                email_handler = EmailHandler(server=imap_server, port=993, username=username, password=password)
                
                if email_handler.connect():
                    email_handler.disconnect()
                    return jsonify({'success': True, 'message': 'Email-Verbindung erfolgreich'})
                else:
                    return jsonify({'success': False, 'error': 'Verbindung fehlgeschlagen'}), 400
                    
            except Exception as e:
                logger.error(f"Fehler beim Testen der Email-Verbindung: {e}", exc_info=True)
                return jsonify({'success': False, 'error': str(e)}), 500
    
    def run(self):
        """Startet den Web-Server"""
        host = self.config.get('web.host', '0.0.0.0')
        port = self.config.get('web.port', 8080)
        debug = self.config.get('web.debug', False)
        self.app.run(host=host, port=port, debug=debug, threaded=True)

