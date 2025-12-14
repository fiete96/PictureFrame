"""
Webinterface für Picture Frame
Ermöglicht Remote-Verwaltung über Browser
"""
from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, send_from_directory, Response
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
import zipfile
import subprocess

from config_manager import ConfigManager
from image_processor import ImageProcessor
from exif_extractor import ExifExtractor
from playlist_manager import PlaylistManager
import threading
import time
import queue

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
        
        # Upload-Queue für verzögerte Verarbeitung
        self._upload_queue = []
        self._upload_queue_lock = threading.Lock()
        self._processing_timer = None
        self._processing_delay = 15.0  # 15 Sekunden warten nach letztem Upload (längere Pause)
        self._processing_batch_size = 5  # Max. 5 Bilder auf einmal verarbeiten
        self._is_processing = False  # Flag um parallele Verarbeitung zu verhindern
        self._upload_in_progress = False  # Flag: Läuft gerade ein Upload?
        self._upload_in_progress_lock = threading.Lock()  # Lock für Upload-Flag
        
        self.setup_routes()
    
    def _schedule_processing(self):
        """Plant die Verarbeitung der Upload-Queue (nach Verzögerung)"""
        # Stoppe alten Timer falls vorhanden
        if self._processing_timer:
            self._processing_timer.cancel()
        
        # Starte neuen Timer (warte auf weitere Uploads)
        self._processing_timer = threading.Timer(self._processing_delay, self._process_upload_queue)
        self._processing_timer.daemon = True
        self._processing_timer.start()
    
    def _process_upload_queue(self):
        """Verarbeitet Bilder in der Upload-Queue in kleinen Batches"""
        # Verhindere parallele Verarbeitung
        if self._is_processing:
            logger.debug("Verarbeitung läuft bereits, überspringe...")
            return
        
        # Prüfe, ob gerade ein Upload läuft - wenn ja, verschiebe Verarbeitung
        with self._upload_in_progress_lock:
            if self._upload_in_progress:
                logger.info("Upload läuft gerade, verschiebe Verarbeitung...")
                # Plane Verarbeitung erneut (nach Verzögerung)
                self._schedule_processing()
                return
        
        with self._upload_queue_lock:
            if not self._upload_queue:
                return
            
            # Nimm nur einen Batch (max. 5 Bilder)
            batch_size = min(self._processing_batch_size, len(self._upload_queue))
            batch = self._upload_queue[:batch_size]
            self._upload_queue = self._upload_queue[batch_size:]
            queue_remaining = len(self._upload_queue)
        
        self._is_processing = True
        
        try:
            logger.info(f"Verarbeite Batch von {batch_size} Bildern (noch {queue_remaining} in Queue)...")
            
            proxy_dir = Path(self.config.get('paths.proxy_images'))
            metadata_file = proxy_dir / 'metadata.json'
            
            # Lade Metadaten einmal (wird für alle Bilder verwendet)
            metadata = {}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception as e:
                    logger.error(f"Fehler beim Laden der Metadaten: {e}")
                    metadata = {}
            
            # Verarbeite jedes Bild nacheinander (nicht parallel!)
            for item in batch:
                try:
                    original_path = item['original_path']
                    uploader_name = item['uploader_name']
                    
                    logger.info(f"Verarbeite Bild: {original_path.name}")
                    
                    # Proxy erstellen
                    proxy_path = self.image_processor.process_image(original_path, proxy_dir)
                    
                    # EXIF-Daten extrahieren
                    exif_data = ExifExtractor.extract_all_exif(original_path)
                    
                    # Metadaten aktualisieren
                    image_hash = proxy_path.stem
                    metadata[image_hash] = {
                        'sender': uploader_name,
                        'subject': '',
                        'date': exif_data.get('date') or datetime.now().isoformat(),
                        'location': exif_data.get('location'),
                        'latitude': exif_data.get('latitude'),
                        'longitude': exif_data.get('longitude'),
                        'exif_data': exif_data
                    }
                    
                    # Speicherfreigabe nach jedem Bild
                    import gc
                    gc.collect()
                    
                except Exception as e:
                    logger.error(f"Fehler beim Verarbeiten von {original_path}: {e}", exc_info=True)
                    continue
            
            # Speichere Metadaten einmal für alle Bilder im Batch
            try:
                metadata_file.parent.mkdir(parents=True, exist_ok=True)
                with open(metadata_file, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                logger.info(f"Metadaten gespeichert für {batch_size} Bilder")
            except Exception as e:
                logger.error(f"Fehler beim Speichern der Metadaten: {e}", exc_info=True)
            
            # Playlists aktualisieren (einmal für alle neuen Bilder im Batch)
            try:
                playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                for item in batch:
                    try:
                        original_path = item['original_path']
                        # Hash aus Proxy-Datei ermitteln
                        file_hash = self.image_processor._get_file_hash(original_path)
                        playlist_manager.add_image(file_hash)
                    except Exception as e:
                        logger.warning(f"Fehler beim Hinzufügen zu Playlist: {e}")
                logger.info(f"Playlists aktualisiert für {batch_size} Bilder")
            except Exception as e:
                logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
            
            # Cache invalidieren
            self._images_cache = None
            self._images_cache_time = None
            
            # Explizite Speicherfreigabe nach Batch
            import gc
            gc.collect()
            
            logger.info(f"Verarbeitung von {batch_size} Bildern abgeschlossen (noch {queue_remaining} in Queue).")
            
            # Signalisiere GUI, dass neue Bilder vorhanden sind (nur wenn Queue leer ist)
            if queue_remaining == 0:
                if self.settings_queue:
                    try:
                        self.settings_queue.put('reload_settings', block=False)
                        logger.info("Reload-Signal an GUI gesendet nach Upload-Verarbeitung")
                    except queue.Full:
                        logger.warning("Settings-Queue voll, überspringe Reload-Signal")
                    except Exception as e:
                        logger.error(f"Fehler beim Senden des Reload-Signals nach Upload: {e}")
            
            # Wenn noch Bilder in der Queue sind, verarbeite den nächsten Batch
            if queue_remaining > 0:
                # Prüfe erneut, ob Upload läuft
                with self._upload_in_progress_lock:
                    if self._upload_in_progress:
                        logger.info("Upload läuft während Verarbeitung, verschiebe nächsten Batch...")
                        self._schedule_processing()  # Plane Verarbeitung erneut
                    else:
                        logger.info(f"Verarbeite nächsten Batch in 2 Sekunden...")
                        time.sleep(2)  # Kurze Pause zwischen Batches
                        self._process_upload_queue()  # Rekursiver Aufruf für nächsten Batch
        finally:
            self._is_processing = False
    
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
        
        @self.app.route('/api/images/bulk-delete', methods=['POST'])
        def bulk_delete_images():
            """Löscht mehrere Bilder auf einmal"""
            try:
                data = request.get_json()
                if not data or 'filenames' not in data:
                    return jsonify({'success': False, 'error': 'Keine Dateinamen übergeben'}), 400
                
                filenames = data['filenames']
                if not isinstance(filenames, list) or len(filenames) == 0:
                    return jsonify({'success': False, 'error': 'Ungültige Dateinamen-Liste'}), 400
                
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                original_dir = Path(self.config.get('paths.original_images'))
                metadata_file = proxy_dir / 'metadata.json'
                
                deleted_count = 0
                errors = []
                
                for filename in filenames:
                    try:
                        proxy_file = proxy_dir / secure_filename(filename)
                        if not proxy_file.exists():
                            errors.append(f"{filename}: Nicht gefunden")
                            continue
                        
                        proxy_hash = proxy_file.stem
                        
                        # Proxy-Datei löschen
                        proxy_file.unlink()
                        logger.info(f"Proxy-Datei gelöscht: {proxy_file.name}")
                        
                        # Original-Datei finden und löschen
                        original_found = False
                        for orig_file in original_dir.rglob("*"):
                            if orig_file.is_file():
                                try:
                                    orig_hash = self.image_processor._get_file_hash(orig_file)
                                    if orig_hash == proxy_hash:
                                        orig_file.unlink()
                                        logger.info(f"Original-Datei gelöscht: {orig_file.name}")
                                        original_found = True
                                        break
                                except Exception:
                                    continue
                        
                        # Metadaten löschen
                        if metadata_file.exists():
                            try:
                                with open(metadata_file, 'r', encoding='utf-8') as f:
                                    metadata = json.load(f)
                                if proxy_hash in metadata:
                                    del metadata[proxy_hash]
                                    with open(metadata_file, 'w', encoding='utf-8') as f:
                                        json.dump(metadata, f, indent=2, ensure_ascii=False)
                            except Exception as e:
                                logger.warning(f"Fehler beim Löschen der Metadaten: {e}")
                        
                        # Playlists aktualisieren
                        try:
                            playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                            playlist_manager.remove_image(proxy_hash)
                        except Exception as e:
                            logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                        
                        # Thumbnail löschen
                        thumbnail_path = self.thumbnail_dir / secure_filename(filename)
                        if thumbnail_path.exists():
                            try:
                                thumbnail_path.unlink()
                            except Exception:
                                pass
                        
                        deleted_count += 1
                    except Exception as e:
                        logger.error(f"Fehler beim Löschen von {filename}: {e}", exc_info=True)
                        errors.append(f"{filename}: {str(e)}")
                
                # Cache invalidieren
                self._images_cache = None
                self._images_cache_time = None
                
                return jsonify({
                    'success': True,
                    'deleted_count': deleted_count,
                    'total_requested': len(filenames),
                    'errors': errors if errors else None
                })
            except Exception as e:
                logger.error(f"Fehler beim Bulk-Löschen: {e}", exc_info=True)
                return jsonify({'success': False, 'error': str(e)}), 500
        
        @self.app.route('/api/images/bulk-download', methods=['POST'])
        def bulk_download_images():
            """Erstellt ein ZIP-Archiv mit mehreren Bildern"""
            try:
                data = request.get_json()
                if not data or 'filenames' not in data:
                    return jsonify({'success': False, 'error': 'Keine Dateinamen übergeben'}), 400
                
                filenames = data['filenames']
                if not isinstance(filenames, list) or len(filenames) == 0:
                    return jsonify({'success': False, 'error': 'Ungültige Dateinamen-Liste'}), 400
                
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                original_dir = Path(self.config.get('paths.original_images'))
                
                # Erstelle temporäres ZIP-Archiv im Speicher
                zip_buffer = io.BytesIO()
                
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for filename in filenames:
                        try:
                            proxy_file = proxy_dir / secure_filename(filename)
                            if not proxy_file.exists():
                                continue
                            
                            proxy_hash = proxy_file.stem
                            
                            # Finde Original-Datei
                            original_file = None
                            for orig_file in original_dir.rglob("*"):
                                if orig_file.is_file():
                                    try:
                                        orig_hash = self.image_processor._get_file_hash(orig_file)
                                        if orig_hash == proxy_hash:
                                            original_file = orig_file
                                            break
                                    except Exception:
                                        continue
                            
                            # Füge Original-Datei zum ZIP hinzu (falls gefunden)
                            if original_file and original_file.exists():
                                zip_file.write(original_file, original_file.name)
                            else:
                                # Fallback: Proxy-Datei verwenden
                                zip_file.write(proxy_file, proxy_file.name)
                        except Exception as e:
                            logger.warning(f"Fehler beim Hinzufügen von {filename} zum ZIP: {e}")
                            continue
                
                zip_buffer.seek(0)
                
                return send_file(
                    zip_buffer,
                    mimetype='application/zip',
                    as_attachment=True,
                    download_name=f'bilder_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
                )
            except Exception as e:
                logger.error(f"Fehler beim Erstellen des ZIP-Archivs: {e}", exc_info=True)
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
            """Lädt ein Bild hoch (schnelle Antwort für iOS-Kompatibilität)"""
            try:
                if 'file' not in request.files:
                    logger.warning("Upload-Anfrage ohne Datei")
                    return jsonify({'success': False, 'error': 'Keine Datei'}), 400
                
                file = request.files['file']
                if file.filename == '':
                    logger.warning("Upload-Anfrage mit leerem Dateinamen")
                    return jsonify({'success': False, 'error': 'Keine Datei ausgewählt'}), 400
                
                # Prüfe Dateigröße vor dem Speichern (iOS-Kompatibilität)
                file.seek(0, 2)  # Zum Ende springen
                file_size = file.tell()
                file.seek(0)  # Zurück zum Anfang
                
                max_size = 16 * 1024 * 1024  # 16MB
                if file_size > max_size:
                    logger.warning(f"Datei zu groß: {file_size} bytes (max: {max_size})")
                    return jsonify({'success': False, 'error': f'Datei zu groß ({file_size // 1024 // 1024}MB, max 16MB)'}), 413
                
                if not self.image_processor.is_supported(Path(file.filename)):
                    logger.warning(f"Dateiformat nicht unterstützt: {file.filename}")
                    return jsonify({'success': False, 'error': 'Dateiformat nicht unterstützt'}), 400
                
                # Markiere, dass Upload läuft (verhindert Verarbeitung während Übertragung)
                with self._upload_in_progress_lock:
                    self._upload_in_progress = True
                
                try:
                    # Original speichern (schnell, ohne Verarbeitung)
                    original_dir = Path(self.config.get('paths.original_images'))
                    original_dir.mkdir(parents=True, exist_ok=True)  # Stelle sicher, dass Verzeichnis existiert
                    
                    filename = secure_filename(file.filename)
                    original_path = original_dir / filename
                    
                    # Falls Datei existiert, Nummer anhängen
                    counter = 1
                    while original_path.exists():
                        stem = Path(filename).stem
                        suffix = Path(filename).suffix
                        original_path = original_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                    
                    # Speichere Datei (schnell)
                    logger.info(f"Speichere Upload: {original_path.name} ({file_size // 1024}KB)")
                    file.save(str(original_path))
                    
                    # Verifiziere, dass Datei gespeichert wurde
                    if not original_path.exists():
                        logger.error(f"Datei konnte nicht gespeichert werden: {original_path}")
                        return jsonify({'success': False, 'error': 'Fehler beim Speichern'}), 500
                    
                    # Name aus POST-Request holen (falls vorhanden)
                    uploader_name = request.form.get('name', 'Manueller Upload')
                    if not uploader_name or uploader_name.strip() == '':
                        uploader_name = 'Manueller Upload'
                    
                    # Zur Verarbeitungs-Queue hinzufügen (verzögerte Verarbeitung)
                    with self._upload_queue_lock:
                        self._upload_queue.append({
                            'original_path': original_path,
                            'uploader_name': uploader_name.strip()
                        })
                        queue_size = len(self._upload_queue)
                        logger.info(f"Bild zur Verarbeitungs-Queue hinzugefügt: {original_path.name} (Queue-Größe: {queue_size})")
                    
                    # Timer zurücksetzen (warte auf weitere Uploads - längere Pause)
                    self._schedule_processing()
                finally:
                    # Upload abgeschlossen - Flag zurücksetzen
                    with self._upload_in_progress_lock:
                        self._upload_in_progress = False
                    logger.debug("Upload-Flag zurückgesetzt")
                
                # Sofortige Antwort (wichtig für iOS-Kompatibilität)
                return jsonify({
                    'success': True, 
                    'message': 'Bild erfolgreich hochgeladen',
                    'filename': original_path.name,
                    'size': file_size
                }), 200
                
            except Exception as e:
                logger.error(f"Fehler beim Upload: {e}", exc_info=True)
                return jsonify({'success': False, 'error': f'Upload-Fehler: {str(e)}'}), 500
        
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
            import shutil
            import os
            
            proxy_dir = Path(self.config.get('paths.proxy_images'))
            original_dir = Path(self.config.get('paths.original_images'))
            
            # Zähle Bilder
            proxy_count = len(list(proxy_dir.glob("*.jpg"))) if proxy_dir.exists() else 0
            original_files = [f for f in original_dir.rglob("*") if f.is_file() and self.image_processor.is_supported(f)] if original_dir.exists() else []
            original_count = len(original_files)
            
            # Duplikat-Berechnung deaktiviert (zu langsam bei vielen Bildern)
            # Die Hash-Berechnung für jedes Bild würde zu lange dauern
            # Verwende Proxy-Count als Näherung für eindeutige Bilder
            duplicates = 0
            unique_images = proxy_count  # Näherung: Proxy-Count entspricht eindeutigen Bildern
            
            # Berechne Speicherbedarf
            def get_dir_size(path):
                """Berechnet die Größe eines Verzeichnisses in Bytes"""
                total = 0
                try:
                    for entry in path.rglob('*'):
                        if entry.is_file():
                            total += entry.stat().st_size
                except Exception as e:
                    logger.warning(f"Fehler beim Berechnen der Verzeichnisgröße: {e}")
                return total
            
            original_size = get_dir_size(original_dir) if original_dir.exists() else 0
            proxy_size = get_dir_size(proxy_dir) if proxy_dir.exists() else 0
            total_size = original_size + proxy_size
            
            # Berechne Speicherkapazität (verwende das Verzeichnis, in dem die Bilder gespeichert sind)
            try:
                # Finde das Root-Verzeichnis (normalerweise das Parent von original_dir)
                root_path = original_dir.parent if original_dir.exists() else Path('.')
                stat = shutil.disk_usage(root_path)
                total_disk = stat.total
                used_disk = stat.used
                free_disk = stat.free
            except Exception as e:
                logger.warning(f"Fehler beim Ermitteln der Speicherkapazität: {e}")
                total_disk = 0
                used_disk = 0
                free_disk = 0
            
            # Formatierung für Anzeige
            def format_bytes(bytes_val):
                """Formatiert Bytes in lesbare Einheit"""
                for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                    if bytes_val < 1024.0:
                        return f"{bytes_val:.2f} {unit}"
                    bytes_val /= 1024.0
                return f"{bytes_val:.2f} PB"
            
            return jsonify({
                'proxy_images': proxy_count,
                'original_images': original_count,
                'unique_images': unique_images,
                'duplicates': duplicates,
                'storage': {
                    'original_size': original_size,
                    'proxy_size': proxy_size,
                    'total_size': total_size,
                    'original_size_formatted': format_bytes(original_size),
                    'proxy_size_formatted': format_bytes(proxy_size),
                    'total_size_formatted': format_bytes(total_size),
                    'disk_total': total_disk,
                    'disk_used': used_disk,
                    'disk_free': free_disk,
                    'disk_total_formatted': format_bytes(total_disk),
                    'disk_used_formatted': format_bytes(used_disk),
                    'disk_free_formatted': format_bytes(free_disk),
                    'disk_usage_percent': round((used_disk / total_disk * 100) if total_disk > 0 else 0, 1)
                },
                'config': self.config.get_all()
            })
        
        @self.app.route('/api/system/update', methods=['POST'])
        def system_update():
            """Führt ein Update des Systems durch (Git Pull + Service Restart)"""
            try:
                # Finde das Projekt-Verzeichnis (normalerweise Parent von src)
                project_dir = Path(__file__).parent.parent
                
                update_log = []
                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Update gestartet...")
                
                # Prüfe ob Git installiert ist
                try:
                    subprocess.run(['git', '--version'], capture_output=True, check=True, timeout=5)
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
                    return jsonify({
                        'success': False,
                        'error': 'Git ist nicht installiert oder nicht verfügbar',
                        'logs': update_log
                    }), 500
                
                # Prüfe ob es ein Git-Repository ist
                git_dir = project_dir / '.git'
                if not git_dir.exists():
                    return jsonify({
                        'success': False,
                        'error': 'Kein Git-Repository gefunden',
                        'logs': update_log
                    }), 500
                
                # Sichere config.yaml vor dem Update (wird nicht überschrieben)
                config_file = project_dir / 'config.yaml'
                config_backup = project_dir / 'config.yaml.backup'
                if config_file.exists():
                    import shutil
                    shutil.copy2(config_file, config_backup)
                    update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] config.yaml gesichert")
                
                # Prüfe auf lokale Änderungen
                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Prüfe auf lokale Änderungen...")
                status_result = subprocess.run(
                    ['git', 'status', '--porcelain'],
                    cwd=str(project_dir),
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                has_local_changes = bool(status_result.stdout.strip())
                if has_local_changes:
                    # Prüfe ob es bereits Commits gibt (für stash benötigt)
                    commit_check = subprocess.run(
                        ['git', 'rev-parse', '--verify', 'HEAD'],
                        cwd=str(project_dir),
                        capture_output=True,
                        timeout=5
                    )
                    
                    if commit_check.returncode == 0:
                        # Commits vorhanden, kann stashen
                        update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Lokale Änderungen gefunden. Stashe Änderungen...")
                        stash_result = subprocess.run(
                            ['git', 'stash', 'push', '-m', f'Auto-stash vor Update {datetime.now().isoformat()}'],
                            cwd=str(project_dir),
                            capture_output=True,
                            text=True,
                            timeout=10
                        )
                        if stash_result.returncode == 0:
                            update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Lokale Änderungen gestasht")
                        else:
                            update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Stash-Warnung: {stash_result.stderr}")
                    else:
                        # Keine Commits vorhanden, versuche initialen Commit zu erstellen
                        update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Keine Commits vorhanden, erstelle initialen Commit...")
                        # Füge nur .gitignore hinzu (config.yaml ist ignoriert)
                        add_result = subprocess.run(
                            ['git', 'add', '.gitignore'],
                            cwd=str(project_dir),
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        if add_result.returncode == 0:
                            commit_result = subprocess.run(
                                ['git', 'commit', '-m', 'Initial commit: Lokale Installation'],
                                cwd=str(project_dir),
                                capture_output=True,
                                text=True,
                                timeout=5
                            )
                            if commit_result.returncode == 0:
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Initialer Commit erstellt")
                            else:
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Commit-Warnung: {commit_result.stderr}")
                
                # Führe Git Pull aus (explizit origin/main angeben)
                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Führe 'git pull origin main' aus...")
                try:
                    # Stelle sicher, dass der Branch richtig konfiguriert ist
                    subprocess.run(
                        ['git', 'branch', '--set-upstream-to=origin/main', 'main'],
                        cwd=str(project_dir),
                        capture_output=True,
                        timeout=5
                    )
                    
                    # Prüfe ob bereits Commits vorhanden sind
                    commit_check = subprocess.run(
                        ['git', 'rev-parse', '--verify', 'HEAD'],
                        cwd=str(project_dir),
                        capture_output=True,
                        timeout=5
                    )
                    
                    if commit_check.returncode == 0:
                        # Normale Pull-Operation
                        result = subprocess.run(
                            ['git', 'pull', 'origin', 'main'],
                            cwd=str(project_dir),
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                    else:
                        # Keine Commits vorhanden, verwende --allow-unrelated-histories
                        update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Keine lokalen Commits, verwende --allow-unrelated-histories...")
                        result = subprocess.run(
                            ['git', 'pull', 'origin', 'main', '--allow-unrelated-histories', '--no-edit'],
                            cwd=str(project_dir),
                            capture_output=True,
                            text=True,
                            timeout=60
                        )
                    
                    if result.stdout:
                        update_log.append(f"Git Output:\n{result.stdout}")
                    if result.stderr:
                        update_log.append(f"Git Warnings:\n{result.stderr}")
                    
                    if result.returncode != 0:
                        update_log.append(f"Git Pull fehlgeschlagen mit Code {result.returncode}")
                        return jsonify({
                            'success': False,
                            'error': f'Git Pull fehlgeschlagen: {result.stderr}',
                            'logs': update_log
                        }), 500
                    
                    update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Git Pull erfolgreich")
                    
                    # Stelle sicher, dass config.yaml nicht überschrieben wurde
                    if config_backup.exists() and config_file.exists():
                        # Prüfe ob config.yaml geändert wurde (durch Git-Pull)
                        try:
                            import filecmp
                            if not filecmp.cmp(config_file, config_backup, shallow=False):
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] config.yaml wurde geändert - stelle Backup wieder her...")
                                shutil.copy2(config_backup, config_file)
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] config.yaml wiederhergestellt")
                            else:
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] config.yaml unverändert")
                        except Exception as e:
                            logger.warning(f"Fehler beim Prüfen der config.yaml: {e}")
                            # Im Zweifel: Backup wiederherstellen
                            if config_backup.exists():
                                shutil.copy2(config_backup, config_file)
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] config.yaml aus Backup wiederhergestellt (Vorsichtsmaßnahme)")
                    
                    # Lösche Backup-Datei
                    if config_backup.exists():
                        try:
                            config_backup.unlink()
                        except:
                            pass
                    
                except subprocess.TimeoutExpired:
                    return jsonify({
                        'success': False,
                        'error': 'Git Pull Timeout (länger als 60 Sekunden)',
                        'logs': update_log
                    }), 500
                except Exception as e:
                    return jsonify({
                        'success': False,
                        'error': f'Fehler beim Git Pull: {str(e)}',
                        'logs': update_log
                    }), 500
                
                # Prüfe ob Änderungen vorhanden waren
                has_changes = 'Already up to date' not in result.stdout
                
                if has_changes:
                    update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Änderungen gefunden, starte Service neu...")
                    
                    # Starte Service neu
                    try:
                        # Versuche zuerst mit sudo -n (non-interactive)
                        restart_result = subprocess.run(
                            ['sudo', '-n', 'systemctl', 'restart', 'pictureframe'],
                            capture_output=True,
                            text=True,
                            timeout=30
                        )
                        
                        if restart_result.returncode == 0:
                            update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service erfolgreich neu gestartet")
                            # Warte kurz und prüfe Status
                            time.sleep(2)
                            status_result = subprocess.run(
                                ['systemctl', 'is-active', 'pictureframe'],
                                capture_output=True,
                                text=True,
                                timeout=5
                            )
                            if status_result.returncode == 0:
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service ist aktiv: {status_result.stdout.strip()}")
                            else:
                                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service-Status Warnung: {status_result.stdout.strip()}")
                        else:
                            error_msg = restart_result.stderr.strip() if restart_result.stderr else restart_result.stdout.strip()
                            update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service-Restart fehlgeschlagen (Code {restart_result.returncode}): {error_msg}")
                            # Versuche Status zu prüfen
                            try:
                                status_result = subprocess.run(
                                    ['systemctl', 'status', 'pictureframe', '--no-pager', '-l'],
                                    capture_output=True,
                                    text=True,
                                    timeout=5
                                )
                                if status_result.stdout:
                                    update_log.append(f"Service-Status:\n{status_result.stdout[:500]}")
                            except:
                                pass
                            
                    except subprocess.TimeoutExpired:
                        update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service-Restart Timeout (länger als 30 Sekunden)")
                    except Exception as e:
                        update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Service-Restart Fehler: {str(e)}")
                        logger.error(f"Fehler beim Service-Restart: {e}", exc_info=True)
                else:
                    update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Keine Änderungen - bereits auf dem neuesten Stand")
                
                update_log.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Update abgeschlossen")
                
                return jsonify({
                    'success': True,
                    'has_changes': has_changes,
                    'logs': update_log
                })
                
            except Exception as e:
                logger.error(f"Fehler beim Update: {e}", exc_info=True)
                return jsonify({
                    'success': False,
                    'error': str(e),
                    'logs': update_log if 'update_log' in locals() else []
                }), 500
        
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

