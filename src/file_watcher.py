"""
File-Watcher für Picture Frame
Überwacht das Proxy-Verzeichnis auf neue Bilder
"""
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from pathlib import Path
import logging
import threading
import time

logger = logging.getLogger(__name__)

class ImageFileHandler(FileSystemEventHandler):
    """Handler für Datei-Ereignisse im Proxy-Verzeichnis"""
    
    def __init__(self, slideshow, slideshow_widget=None, refresh_signal=None):
        self.slideshow = slideshow
        self.slideshow_widget = slideshow_widget
        self.proxy_dir = slideshow.proxy_dir
        self.refresh_signal = refresh_signal  # PyQt5 Signal für thread-sichere Kommunikation
        # Thread-sicherer Debounce-Timer (verwendet threading.Timer statt QTimer)
        self._refresh_timer = None
        self._refresh_lock = threading.Lock()
        self._last_refresh_time = 0
        self._debounce_interval = 5.0  # 5 Sekunden Debounce (erhöht für Bulk-Uploads)
    
    def _perform_refresh(self):
        """Führt den eigentlichen Slideshow-Refresh aus (wird im Haupt-Thread aufgerufen)"""
        # Diese Methode wird nicht mehr verwendet, da wir direkt das Signal emittieren
        # Behalten für Fallback-Fall
        pass
    
    def _trigger_refresh(self):
        """Startet oder setzt den Refresh-Timer zurück (thread-sicher)"""
        with self._refresh_lock:
            current_time = time.time()
            # Wenn letzter Refresh zu kurz her ist, Timer zurücksetzen
            if self._refresh_timer:
                self._refresh_timer.cancel()
            
            # Neuen Timer starten
            self._refresh_timer = threading.Timer(self._debounce_interval, self._schedule_refresh)
            self._refresh_timer.daemon = True
            self._refresh_timer.start()
    
    def _schedule_refresh(self):
        """Plant den Refresh im Haupt-Thread über Signal"""
        if self.refresh_signal:
            # Emittiere Signal (wird im Haupt-Thread verarbeitet)
            self.refresh_signal.emit()
        else:
            # Fallback: Direkter Aufruf (nicht thread-sicher, aber besser als nichts)
            logger.warning("Kein Refresh-Signal verfügbar, verwende direkten Aufruf")
            self._perform_refresh()
    
    def on_created(self, event):
        """Wird aufgerufen, wenn eine neue Datei erstellt wird"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix.lower() == '.jpg' and file_path.parent == self.proxy_dir:
            logger.info(f"Neues Bild erkannt: {file_path.name}")
            # Verwende Debouncing, um mehrere Events zu bündeln
            self._trigger_refresh()
    
    def on_modified(self, event):
        """Wird aufgerufen, wenn eine Datei modifiziert wird"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix.lower() == '.jpg' and file_path.parent == self.proxy_dir:
            # Prüfe ob Datei bereits in der Liste ist
            file_exists = any(img.name == file_path.name for img in self.slideshow.images)
            if not file_exists:
                logger.info(f"Neues Bild erkannt (modified): {file_path.name}")
                # Verwende Debouncing, um mehrere Events zu bündeln
                self._trigger_refresh()
    
    def on_deleted(self, event):
        """Wird aufgerufen, wenn eine Datei gelöscht wird"""
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix.lower() == '.jpg' and file_path.parent == self.proxy_dir:
            logger.info(f"Bild gelöscht erkannt: {file_path.name}")
            
            # Prüfe ob das gelöschte Bild das aktuelle Bild war
            current_image = self.slideshow.get_current_image()
            was_current = current_image and current_image == file_path
            
            # Slideshow aktualisieren
            old_count = len(self.slideshow.images)
            self.slideshow.refresh()
            
            # Wenn das aktuelle Bild gelöscht wurde, zum nächsten Bild wechseln
            if was_current and len(self.slideshow.images) > 0:
                # Wenn keine Bilder mehr vorhanden, bleibt Index 0 (zeigt Platzhalter)
                if self.slideshow.current_index >= len(self.slideshow.images):
                    self.slideshow.current_index = 0
            
            # UI aktualisieren, falls verfügbar (thread-sicher)
            if self.slideshow_widget:
                try:
                    # Verwende safe_refresh für Thread-sicherheit
                    if hasattr(self.slideshow_widget, 'safe_refresh'):
                        self.slideshow_widget.safe_refresh()
                    else:
                        self.slideshow_widget.refresh()
                    logger.info(f"Slideshow aktualisiert nach Löschung: {old_count} -> {len(self.slideshow.images)} Bilder")
                except Exception as e:
                    logger.error(f"Fehler beim Aktualisieren der Slideshow: {e}")

class FileWatcher:
    """Überwacht das Proxy-Verzeichnis auf neue Bilder"""
    
    def __init__(self, proxy_dir: Path, slideshow, slideshow_widget=None, refresh_signal=None):
        self.proxy_dir = Path(proxy_dir)
        self.slideshow = slideshow
        self.slideshow_widget = slideshow_widget
        self.refresh_signal = refresh_signal  # PyQt5 Signal für thread-sichere Kommunikation
        self.observer = None
    
    def start(self):
        """Startet die Überwachung"""
        if not self.proxy_dir.exists():
            self.proxy_dir.mkdir(parents=True, exist_ok=True)
        
        event_handler = ImageFileHandler(
            self.slideshow, 
            self.slideshow_widget,
            refresh_signal=self.refresh_signal
        )
        self.observer = Observer()
        self.observer.schedule(event_handler, str(self.proxy_dir), recursive=False)
        self.observer.start()
        logger.info(f"File-Watcher gestartet für: {self.proxy_dir}")
    
    def stop(self):
        """Stoppt die Überwachung"""
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("File-Watcher gestoppt")

