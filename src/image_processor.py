"""
Bildverarbeitung für Picture Frame
Konvertiert Bilder in optimale Proxy-Versionen für den Bildschirm
"""
from PIL import Image
from pathlib import Path
import hashlib
import logging

logger = logging.getLogger(__name__)

class ImageProcessor:
    def __init__(self, target_width: int = 1024, target_height: int = 600, quality: int = 85):
        self.target_width = target_width
        self.target_height = target_height
        self.quality = quality
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'}
    
    def process_image(self, source_path: Path, proxy_dir: Path) -> Path:
        """
        Verarbeitet ein Bild und erstellt eine Proxy-Version
        
        Args:
            source_path: Pfad zum Originalbild
            proxy_dir: Verzeichnis für Proxy-Bilder
            
        Returns:
            Pfad zum erstellten Proxy-Bild
        """
        try:
            # Hash des Originalbilds für eindeutigen Dateinamen
            file_hash = self._get_file_hash(source_path)
            proxy_path = proxy_dir / f"{file_hash}.jpg"
            
            # Wenn Proxy bereits existiert, überspringen
            if proxy_path.exists():
                logger.info(f"Proxy bereits vorhanden: {proxy_path}")
                return proxy_path
            
            # Bild öffnen und verarbeiten
            with Image.open(source_path) as img:
                # EXIF-Orientierung korrigieren
                img = self._fix_orientation(img)
                
                # Bild auf Zielgröße anpassen (mit Seitenverhältnis)
                img = self._resize_with_aspect_ratio(img)
                
                # In RGB konvertieren (falls nötig)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # Proxy-Bild speichern
                img.save(proxy_path, 'JPEG', quality=self.quality, optimize=True)
                logger.info(f"Proxy-Bild erstellt: {proxy_path}")
                return proxy_path
                
        except Exception as e:
            logger.error(f"Fehler beim Verarbeiten von {source_path}: {e}")
            raise
    
    def _fix_orientation(self, img: Image.Image) -> Image.Image:
        """Korrigiert die Bildorientierung basierend auf EXIF-Daten"""
        try:
            # EXIF-Orientierung (Tag 274)
            exif = img.getexif()
            if exif is not None:
                orientation = exif.get(274)  # ORIENTATION tag number
                if orientation == 3:
                    img = img.rotate(180, expand=True)
                elif orientation == 6:
                    img = img.rotate(270, expand=True)
                elif orientation == 8:
                    img = img.rotate(90, expand=True)
        except (AttributeError, KeyError, TypeError, Exception):
            pass
        return img
    
    def _resize_with_aspect_ratio(self, img: Image.Image) -> Image.Image:
        """Passt Bildgröße an, behält Seitenverhältnis bei"""
        img_width, img_height = img.size
        target_ratio = self.target_width / self.target_height
        img_ratio = img_width / img_height
        
        if img_ratio > target_ratio:
            # Bild ist breiter - an Breite anpassen
            new_width = self.target_width
            new_height = int(self.target_width / img_ratio)
        else:
            # Bild ist höher - an Höhe anpassen
            new_height = self.target_height
            new_width = int(self.target_height * img_ratio)
        
        # Hochwertiges Resampling verwenden
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Falls nötig, auf exakte Größe zentrieren und zuschneiden
        if new_width != self.target_width or new_height != self.target_height:
            left = (new_width - self.target_width) // 2
            top = (new_height - self.target_height) // 2
            right = left + self.target_width
            bottom = top + self.target_height
            img = img.crop((left, top, right, bottom))
        
        return img
    
    def _get_file_hash(self, file_path: Path) -> str:
        """Erstellt einen Hash-Wert für die Datei"""
        hash_md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    
    def is_supported(self, file_path: Path) -> bool:
        """Prüft, ob das Dateiformat unterstützt wird"""
        return file_path.suffix.lower() in self.supported_formats

