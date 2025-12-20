"""
Hauptanwendung für Picture Frame
Startet UI und Webinterface parallel
"""
import sys
import os
import threading
import logging
import queue
from pathlib import Path

# Pfad für Imports hinzufügen
sys.path.insert(0, str(Path(__file__).parent))

from config_manager import ConfigManager
from image_processor import ImageProcessor
from web_interface import WebInterface

# GUI-Imports nur wenn nötig
MainWindow = None
QApplication = None

def setup_logging():
    """Richtet das Logging für Production ein"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Production: INFO-Level, keine DEBUG-Logs
    # Log-Rotation: Max. 5 Dateien à 10MB
    from logging.handlers import RotatingFileHandler
    
    file_handler = RotatingFileHandler(
        log_dir / 'pictureframe.log',
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)  # Nur Warnings/Errors in Console
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )

# Globale Queue für Settings-Updates (Thread-sichere Kommunikation)
_settings_queue = queue.Queue()

def run_web_interface(config, image_processor):
    """Startet das Webinterface in einem separaten Thread"""
    web = WebInterface(config, image_processor, settings_queue=_settings_queue)
    web.run()

def main():
    """Hauptfunktion"""
    setup_logging()
    logger = logging.getLogger(__name__)
    
    # Prüfe ob bereits eine Instanz läuft
    import fcntl
    lock_file = Path("/tmp/pictureframe.lock")
    
    # Prüfe zuerst, ob Lock-File existiert und ob die PID noch läuft
    if lock_file.exists():
        try:
            with open(lock_file, 'r') as f:
                old_pid = int(f.read().strip())
            # Prüfe ob Prozess noch läuft
            try:
                os.kill(old_pid, 0)  # Signal 0 prüft nur Existenz
                # Prozess läuft noch
                # Prüfe ob wir von Systemd gestartet wurden (dann sollten wir die alte Instanz beenden)
                if os.environ.get('INVOCATION_ID'):
                    # Wir wurden von Systemd gestartet - beende die alte Instanz
                    logger.info(f"Alte Instanz (PID: {old_pid}) läuft noch, beende sie...")
                    try:
                        os.kill(old_pid, 15)  # SIGTERM
                        import time
                        time.sleep(2)
                        # Prüfe ob Prozess noch läuft
                        try:
                            os.kill(old_pid, 0)
                            # Läuft noch, verwende SIGKILL
                            logger.warning(f"Alte Instanz reagiert nicht auf SIGTERM, verwende SIGKILL...")
                            os.kill(old_pid, 9)  # SIGKILL
                            time.sleep(1)
                        except ProcessLookupError:
                            logger.info(f"Alte Instanz erfolgreich beendet")
                    except Exception as e:
                        logger.error(f"Fehler beim Beenden der alten Instanz: {e}")
                        sys.exit(1)  # Fehler, damit Systemd neu startet
                else:
                    # Nicht von Systemd gestartet - beende diese Instanz
                    logger.warning(f"Eine andere Instanz von PictureFrame läuft bereits (PID: {old_pid}). Beende.")
                    sys.exit(0)  # Exit-Code 0 = Erfolg, keine weitere Aktion nötig
            except ProcessLookupError:
                # Prozess existiert nicht mehr, Lock-File ist veraltet
                logger.info(f"Altes Lock-File gefunden (PID: {old_pid}), aber Prozess läuft nicht mehr. Lösche Lock-File.")
                lock_file.unlink()
            except PermissionError:
                # Keine Berechtigung, aber Prozess existiert wahrscheinlich
                logger.warning(f"Eine andere Instanz von PictureFrame läuft möglicherweise (PID: {old_pid}). Beende.")
                sys.exit(0)  # Exit-Code 0 = Erfolg, keine weitere Aktion nötig
        except (ValueError, IOError):
            # Lock-File ist ungültig, lösche es
            logger.info("Ungültiges Lock-File gefunden, lösche es.")
            try:
                lock_file.unlink()
            except:
                pass
    
    # Versuche Lock zu erstellen
    try:
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        # Datei nicht schließen, damit Lock erhalten bleibt
    except IOError:
        logger.warning("Konnte Lock-File nicht erstellen. Eine andere Instanz läuft möglicherweise. Beende.")
        sys.exit(0)  # Exit-Code 0 = Erfolg, keine weitere Aktion nötig (mit Restart=on-failure wird nicht neu gestartet)
    
    try:
        # Konfiguration laden
        config = ConfigManager()
        image_processor = ImageProcessor(
            target_width=config.get('display.width', 1024),
            target_height=config.get('display.height', 600)
        )
        
        # Webinterface im Hintergrund starten (wird später mit Callback aktualisiert)
        web_thread = threading.Thread(
            target=run_web_interface,
            args=(config, image_processor),
            daemon=True
        )
        web_thread.start()
        logger.info("Webinterface gestartet")
        
        # Haupt-UI starten (nur wenn Display verfügbar)
        # Warte auf X11/Display (max. 60 Sekunden)
        display_available = False
        import subprocess
        import time as time_module
        
        logger.info("Warte auf Display/X11...")
        display = os.environ.get('DISPLAY', ':0')
        os.environ['DISPLAY'] = display
        
        # Warte auf X11 (max. 60 Sekunden beim Booten)
        # 30 Versuche à 2 Sekunden = 60 Sekunden max
        max_attempts = 30
        check_interval = 2.0
        
        for attempt in range(max_attempts):
            try:
                # Prüfe ob X11 läuft
                result = subprocess.run(['xdpyinfo'], 
                                       capture_output=True, 
                                       timeout=1.0,
                                       env=os.environ)
                if result.returncode == 0:
                    display_available = True
                    logger.info(f"Display {display} gefunden und verfügbar (nach {attempt * check_interval:.1f}s)")
                    break
            except Exception:
                # Display-Check fehlgeschlagen, weiter versuchen
                pass
            
            if attempt < max_attempts - 1:
                time_module.sleep(check_interval)
        
        if display_available:
            try:
                # GUI-Module importieren (erst jetzt, wenn Display sicher verfügbar)
                logger.info("Importiere GUI-Module...")
                from main_ui import MainWindow
                from PyQt5.QtWidgets import QApplication
                from PyQt5.QtCore import Qt
                
                app = QApplication(sys.argv)
                app.setApplicationName("Picture Frame")
                
                # Konfiguriere Emoji-Font-Unterstützung
                try:
                    from PyQt5.QtGui import QFontDatabase
                    # Versuche Emoji-Fonts zu laden
                    font_db = QFontDatabase()
                    # Prüfe verfügbare Fonts für Emoji-Unterstützung
                    emoji_fonts = ['Noto Color Emoji', 'Noto Emoji', 'Apple Color Emoji', 'Segoe UI Emoji']
                    emoji_font_found = False
                    for font_name in emoji_fonts:
                        if font_db.hasFamily(font_name):
                            logger.info(f"Emoji-Font gefunden: {font_name}")
                            emoji_font_found = True
                            break
                    
                    if not emoji_font_found:
                        logger.warning("Kein Emoji-Font gefunden. Emojis werden möglicherweise nicht korrekt angezeigt.")
                        logger.info("Installieren Sie 'fonts-noto-color-emoji' für Emoji-Unterstützung.")
                except Exception as e:
                    logger.warning(f"Fehler beim Konfigurieren der Emoji-Fonts: {e}")
                
                # Stelle sicher, dass die App einen dunklen Hintergrund hat
                # Nur für QMainWindow und QWidget ohne spezifische Styles
                app.setStyleSheet("""
                    QMainWindow { background-color: #1a1a2e; }
                    QWidget { background-color: #1a1a2e; }
                    QScrollArea { background-color: #1a1a2e; }
                """)
                
                window = MainWindow()
                # Übergebe Queue an MainWindow für Settings-Updates
                window.set_settings_queue(_settings_queue)
                
                # Zeige Fenster SOFORT, bevor langwierige Initialisierungen
                window.show()
                # Stelle sicher, dass das Fenster sofort sichtbar ist
                app.processEvents()
                
                # Stelle sicher, dass Fenster richtig positioniert ist (nach show())
                # Warte kurz, damit X11 das Fenster erstellt hat
                time_module.sleep(0.1)
                app.processEvents()
                
                # Setze Fensterposition explizit (falls nicht im Fullscreen)
                if not window.isFullScreen():
                    screen = app.primaryScreen()
                    screen_geometry = screen.geometry()
                    window_width = window.config.get('display.width', 1024)
                    window_height = window.config.get('display.height', 600)
                    x = (screen_geometry.width() - window_width) // 2
                    y = (screen_geometry.height() - window_height) // 2
                    window.setGeometry(x, y, window_width, window_height)
                    app.processEvents()
                
                logger.info("Hauptanwendung mit GUI gestartet")
                
                # Initialisiere langwierige Komponenten NACH dem Anzeigen des Fensters
                # (Email-Checker, File-Watcher werden bereits verzögert gestartet)
                sys.exit(app.exec_())
            except Exception as e:
                logger.error(f"Fehler beim Starten der GUI: {e}", exc_info=True)
                logger.info("Fahre fort ohne GUI (nur Webinterface)...")
        else:
            logger.warning(f"Kein Display verfügbar nach {max_attempts * check_interval:.0f} Sekunden - starte nur Webinterface")
        
        # Webinterface weiterlaufen lassen (wenn keine GUI)
        port = config.get('web.port', 80)
        logger.info(f"Webinterface läuft auf http://0.0.0.0:{port}")
        while True:
            time_module.sleep(60)
        
    except Exception as e:
        logger.critical(f"Kritischer Fehler beim Starten: {e}", exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    main()

