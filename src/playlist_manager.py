"""
Playlist-Manager für Picture Frame
Verwaltet Playlist-Dateien für verschiedene Sortierungen
"""
from pathlib import Path
import json
import logging
import os
import random
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class PlaylistManager:
    """Verwaltet Playlist-Dateien für verschiedene Sortierungen"""
    
    def __init__(self, proxy_dir: Path, metadata_file: Path):
        self.proxy_dir = proxy_dir
        self.metadata_file = metadata_file
        self.playlist_dir = proxy_dir / 'playlists'
        self.playlist_dir.mkdir(exist_ok=True)
    
    def get_playlist_file(self, sort_by: str) -> Path:
        """Gibt den Pfad zur Playlist-Datei für eine Sortierung zurück"""
        return self.playlist_dir / f"playlist_{sort_by}.json"
    
    def _get_sort_key(self, image_hash: str, sort_by: str) -> float:
        """Gibt den Sortierschlüssel für ein Bild zurück"""
        image_path = self.proxy_dir / f"{image_hash}.jpg"
        
        if sort_by == "transfer_time":
            # Sortierung nach Übertragungszeit (Datei-Modifikationszeit)
            try:
                return os.path.getmtime(image_path) if image_path.exists() else 0
            except OSError:
                return 0
        
        elif sort_by == "creation_time":
            # Sortierung nach Erstellungszeit aus EXIF-Metadaten
            try:
                if self.metadata_file.exists():
                    with open(self.metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        image_metadata = metadata.get(image_hash, {})
                        
                        # Prüfe EXIF-Daten
                        exif_data = image_metadata.get('exif_data', {})
                        if exif_data and exif_data.get('date'):
                            try:
                                return datetime.fromisoformat(exif_data['date']).timestamp()
                            except (ValueError, TypeError):
                                pass
                
                # Fallback: Verwende Datei-Modifikationszeit
                return os.path.getmtime(image_path) if image_path.exists() else 0
            except Exception:
                try:
                    return os.path.getmtime(image_path) if image_path.exists() else 0
                except OSError:
                    return 0
        
        elif sort_by == "random":
            # Zufällige Sortierung (wird beim Laden gemischt)
            # Verwende Hash für konsistente Sortierung
            return hash(image_hash)
        
        else:
            # Fallback: Alphabetisch nach Dateinamen
            return hash(image_hash)
    
    def add_image(self, image_hash: str, sort_by: str = None):
        """Fügt ein Bild zu den Playlists hinzu"""
        if sort_by is None:
            # Füge zu allen Playlists hinzu
            for sort_type in ["transfer_time", "creation_time", "random"]:
                self._add_to_playlist(image_hash, sort_type)
        else:
            self._add_to_playlist(image_hash, sort_by)
    
    def _add_to_playlist(self, image_hash: str, sort_by: str):
        """Fügt ein Bild zu einer spezifischen Playlist hinzu"""
        playlist_file = self.get_playlist_file(sort_by)
        
        # Lade bestehende Playlist
        playlist = []
        if playlist_file.exists():
            try:
                with open(playlist_file, 'r', encoding='utf-8') as f:
                    playlist = json.load(f)
            except Exception as e:
                logger.warning(f"Konnte Playlist nicht laden: {e}")
                playlist = []
        
        # Prüfe ob Bild bereits in Playlist ist (prüfe Hash in Dictionary-Liste)
        if any(item.get('hash') == image_hash for item in playlist):
            return
        
        # Berechne Sortierschlüssel
        sort_key = self._get_sort_key(image_hash, sort_by)
        
        # Füge Bild mit Sortierschlüssel hinzu
        playlist.append({
            'hash': image_hash,
            'sort_key': sort_key
        })
        
        # Sortiere Playlist (neueste zuerst = reverse=True)
        if sort_by == "random":
            # Bei random wird beim Laden gemischt, hier nur hinzufügen
            pass
        else:
            playlist.sort(key=lambda x: x['sort_key'], reverse=True)
        
        # Speichere Playlist
        try:
            with open(playlist_file, 'w', encoding='utf-8') as f:
                json.dump(playlist, f, indent=2)
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Playlist: {e}")
    
    def remove_image(self, image_hash: str):
        """Entfernt ein Bild aus allen Playlists"""
        for sort_type in ["transfer_time", "creation_time", "random"]:
            playlist_file = self.get_playlist_file(sort_type)
            if playlist_file.exists():
                try:
                    with open(playlist_file, 'r', encoding='utf-8') as f:
                        playlist = json.load(f)
                    
                    # Entferne Bild aus Playlist
                    playlist = [item for item in playlist if item.get('hash') != image_hash]
                    
                    with open(playlist_file, 'w', encoding='utf-8') as f:
                        json.dump(playlist, f, indent=2)
                except Exception as e:
                    logger.warning(f"Fehler beim Entfernen aus Playlist: {e}")
    
    def rebuild_playlist(self, sort_by: str):
        """Baut eine Playlist neu auf (z.B. nach Änderung der Sortierung)"""
        playlist_file = self.get_playlist_file(sort_by)
        
        # Sammle alle Bilder
        image_hashes = []
        for image_file in self.proxy_dir.glob("*.jpg"):
            image_hashes.append(image_file.stem)
        
        # Erstelle Playlist mit Sortierschlüsseln
        playlist = []
        for image_hash in image_hashes:
            sort_key = self._get_sort_key(image_hash, sort_by)
            playlist.append({
                'hash': image_hash,
                'sort_key': sort_key
            })
        
        # Sortiere Playlist (neueste zuerst = reverse=True)
        if sort_by == "random":
            random.shuffle(playlist)
        else:
            playlist.sort(key=lambda x: x['sort_key'], reverse=True)
        
        # Speichere Playlist
        try:
            with open(playlist_file, 'w', encoding='utf-8') as f:
                json.dump(playlist, f, indent=2)
            logger.info(f"Playlist {sort_by} neu aufgebaut: {len(playlist)} Bilder")
        except Exception as e:
            logger.error(f"Fehler beim Neuerstellen der Playlist: {e}")
    
    def get_playlist(self, sort_by: str) -> List[str]:
        """Gibt die Playlist für eine Sortierung zurück (nur Hash-Liste)"""
        playlist_file = self.get_playlist_file(sort_by)
        
        if not playlist_file.exists():
            # Playlist existiert nicht - erstelle sie
            self.rebuild_playlist(sort_by)
        
        try:
            with open(playlist_file, 'r', encoding='utf-8') as f:
                playlist = json.load(f)
            
            # Extrahiere nur die Hashes
            return [item.get('hash') for item in playlist if item.get('hash')]
        except Exception as e:
            logger.error(f"Fehler beim Laden der Playlist: {e}")
            # Fallback: Baue Playlist neu auf
            self.rebuild_playlist(sort_by)
            return self.get_playlist(sort_by)
    
    def update_playlist_for_image(self, image_hash: str, sort_by: str = None):
        """Aktualisiert die Playlist für ein Bild (z.B. nach EXIF-Update)"""
        if sort_by is None:
            # Aktualisiere alle Playlists
            for sort_type in ["transfer_time", "creation_time", "random"]:
                self.rebuild_playlist(sort_type)
        else:
            self.rebuild_playlist(sort_by)

