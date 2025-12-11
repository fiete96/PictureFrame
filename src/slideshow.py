"""
Slideshow-Komponente für Picture Frame
"""
from pathlib import Path
import random
import logging
import os
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class Slideshow:
    def __init__(self, proxy_dir: Path, interval_seconds: int = 10, shuffle: bool = False, loop: bool = True, sort_by: str = "transfer_time", original_dir: Optional[Path] = None, metadata_file: Optional[Path] = None):
        self.proxy_dir = Path(proxy_dir)
        self.interval_seconds = interval_seconds
        self.shuffle = shuffle  # Deprecated, wird durch sort_by ersetzt
        self.loop = loop
        self.sort_by = sort_by  # "transfer_time", "creation_time", "random"
        self.original_dir = Path(original_dir) if original_dir else None
        self.metadata_file = Path(metadata_file) if metadata_file else (proxy_dir / 'metadata.json')
        self.current_index = 0
        self.images: List[Path] = []
        self._refresh_image_list()
    
    def _get_image_sort_key(self, image_path: Path):
        """Gibt den Sortierschlüssel für ein Bild zurück"""
        if self.sort_by == "transfer_time":
            # Sortierung nach Übertragungszeit (Datei-Modifikationszeit)
            try:
                return os.path.getmtime(image_path)
            except OSError:
                return 0
        elif self.sort_by == "creation_time":
            # Sortierung nach Erstellungszeit aus EXIF-Metadaten
            try:
                # Versuche EXIF-Datum aus Originalbild zu extrahieren
                if self.original_dir:
                    image_hash = image_path.stem
                    # Suche Originalbild mit gleichem Hash
                    for orig_file in self.original_dir.glob('*'):
                        if orig_file.is_file():
                            try:
                                from image_processor import ImageProcessor
                                processor = ImageProcessor()
                                orig_hash = processor._get_file_hash(orig_file)
                                if orig_hash == image_hash:
                                    # Originalbild gefunden, EXIF-Datum extrahieren
                                    from exif_extractor import ExifExtractor
                                    exif_data = ExifExtractor.extract_all_exif(orig_file)
                                    if exif_data.get('date'):
                                        try:
                                            return datetime.fromisoformat(exif_data['date']).timestamp()
                                        except (ValueError, TypeError):
                                            pass
                            except Exception:
                                pass
                # Fallback: Verwende Datei-Modifikationszeit
                return os.path.getmtime(image_path)
            except Exception:
                # Fallback: Verwende Datei-Modifikationszeit
                try:
                    return os.path.getmtime(image_path)
                except OSError:
                    return 0
        else:
            # Fallback: Alphabetisch nach Dateinamen
            return image_path.name
    
    def _refresh_image_list(self):
        """Aktualisiert die Liste der verfügbaren Bilder (optimiert für schnellen Start mit Playlists)"""
        if not self.proxy_dir.exists():
            self.proxy_dir.mkdir(parents=True, exist_ok=True)
            self.images = []
            self.current_index = 0
            logger.info("Slideshow: Keine Bilder vorhanden")
            return
        
        # Verwende PlaylistManager für schnelles Laden
        try:
            from playlist_manager import PlaylistManager
            playlist_manager = PlaylistManager(self.proxy_dir, self.metadata_file)
            
            # Lade Playlist für aktuelle Sortierung
            image_hashes = playlist_manager.get_playlist(self.sort_by)
            
            # Konvertiere Hashes zu Pfaden
            image_paths = [self.proxy_dir / f"{hash_val}.jpg" for hash_val in image_hashes]
            
            # Filtere nicht existierende Bilder
            image_paths = [p for p in image_paths if p.exists()]
            
            logger.info(f"Slideshow: {len(image_paths)} Bilder aus Playlist geladen (Sortierung: {self.sort_by})")
            self.images = image_paths
        except Exception as e:
            # Fallback: Alte Methode wenn Playlist nicht verfügbar
            logger.warning(f"Konnte Playlist nicht laden, verwende Fallback: {e}")
            image_paths = list(self.proxy_dir.glob("*.jpg"))
            
            # Sortierung anwenden (neueste zuerst = reverse=True)
            if self.sort_by == "random":
                random.shuffle(image_paths)
            elif self.sort_by == "transfer_time":
                image_paths.sort(key=lambda p: os.path.getmtime(p) if p.exists() else 0, reverse=True)
            else:
                # Fallback: Alphabetisch (neueste zuerst)
                image_paths.sort(key=lambda p: p.name, reverse=True)
            
            self.images = image_paths
            logger.info(f"Slideshow: {len(self.images)} Bilder geladen (Sortierung: {self.sort_by}, Fallback-Methode)")
        
        # Index anpassen, falls außerhalb des Bereichs
        if len(self.images) == 0:
            self.current_index = 0
        elif self.current_index >= len(self.images):
            self.current_index = 0
        else:
            # Prüfe nur das aktuelle Bild auf Existenz (lazy check)
            current_image = self.images[self.current_index] if self.current_index < len(self.images) else None
            if current_image and not current_image.exists():
                # Aktuelles Bild wurde gelöscht, zum nächsten wechseln
                self.current_index = min(self.current_index, len(self.images) - 1)
    
    def get_current_image(self) -> Optional[Path]:
        """Gibt den Pfad zum aktuellen Bild zurück (mit Lazy-Existenz-Prüfung)"""
        if not self.images:
            return None
        
        image_path = self.images[self.current_index]
        # Lazy-Existenz-Prüfung: Nur beim Zugriff prüfen
        if not image_path.exists():
            # Bild wurde gelöscht, zum nächsten wechseln
            self._refresh_image_list()
            if self.images:
                image_path = self.images[self.current_index]
            else:
                return None
        
        return image_path
    
    def next_image(self) -> Optional[Path]:
        """Wechselt zum nächsten Bild"""
        if not self.images:
            return None
        
        self.current_index += 1
        
        if self.current_index >= len(self.images):
            if self.loop:
                self.current_index = 0
            else:
                self.current_index = len(self.images) - 1
        
        return self.get_current_image()
    
    def previous_image(self) -> Optional[Path]:
        """Wechselt zum vorherigen Bild"""
        if not self.images:
            return None
        
        self.current_index -= 1
        
        if self.current_index < 0:
            if self.loop:
                self.current_index = len(self.images) - 1
            else:
                self.current_index = 0
        
        return self.get_current_image()
    
    def refresh(self):
        """Aktualisiert die Bildliste (wird aufgerufen, wenn neue Bilder hinzugefügt wurden)"""
        # Optimiert: Nur Playlist neu laden, nicht die gesamte Liste neu erstellen
        # Das verhindert unnötige Speicher-Allokationen
        try:
            from playlist_manager import PlaylistManager
            playlist_manager = PlaylistManager(self.proxy_dir, self.metadata_file)
            
            # Lade aktualisierte Playlist
            image_hashes = playlist_manager.get_playlist(self.sort_by)
            
            # Konvertiere Hashes zu Pfaden
            new_image_paths = [self.proxy_dir / f"{hash_val}.jpg" for hash_val in image_hashes]
            
            # Filtere nicht existierende Bilder
            new_image_paths = [p for p in new_image_paths if p.exists()]
            
            old_count = len(self.images)
            self.images = new_image_paths
            
            # Index anpassen, falls außerhalb des Bereichs
            if len(self.images) == 0:
                self.current_index = 0
            elif self.current_index >= len(self.images):
                self.current_index = 0
            
            if len(self.images) > old_count:
                logger.info(f"Neue Bilder in Slideshow hinzugefügt: {len(self.images) - old_count}")
        except Exception as e:
            # Fallback: Vollständige Neuladung
            logger.warning(f"Fehler beim optimierten Refresh, verwende vollständige Neuladung: {e}")
            old_count = len(self.images)
            self._refresh_image_list()
            if len(self.images) > old_count:
                logger.info(f"Neue Bilder in Slideshow hinzugefügt: {len(self.images) - old_count}")
    
    def get_image_count(self) -> int:
        """Gibt die Anzahl der verfügbaren Bilder zurück"""
        return len(self.images)

