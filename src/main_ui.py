"""
Haupt-UI für Picture Frame
PyQt5-basiertes Interface mit Touch-Unterstützung
"""
import sys
from pathlib import Path
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QPushButton, QListWidget, 
                             QStackedWidget, QMessageBox, QFileDialog, QDialog,
                             QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QTextEdit, QComboBox,
                             QGridLayout, QScrollArea, QGraphicsOpacityEffect)
from PyQt5.QtCore import Qt, QTimer, QSize, pyqtSignal, QEvent, QPointF, QRectF, QPropertyAnimation, QEasingCurve, QRect
from PyQt5.QtGui import QPixmap, QImage, QFont, QPainter, QColor, QPalette, QTouchEvent, QTransform, QPen, QBrush
import socket
import subprocess
import io
import os
import logging
import json
from datetime import datetime
from typing import Optional
from PIL import Image
from PIL.ExifTags import TAGS

from config_manager import ConfigManager
from exif_extractor import ExifExtractor
from playlist_manager import PlaylistManager

# Prüfe QR-Code-Library
try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False
    logging.warning("qrcode library nicht verfügbar. QR-Code wird nicht angezeigt.")
from slideshow import Slideshow
from image_processor import ImageProcessor
from email_handler import EmailHandler
from file_watcher import FileWatcher

logger = logging.getLogger(__name__)

class SlideshowWidget(QWidget):
    """Haupt-Slideshow-Widget mit Touch-Gesten"""
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()
    refresh_requested = pyqtSignal()  # Signal für thread-sichere Aktualisierung
    
    def __init__(self, slideshow: Slideshow, config: ConfigManager, main_window=None):
        super().__init__()
        self.slideshow = slideshow
        self.config = config
        self.main_window = main_window  # Referenz zu MainWindow für Menü
        
        # Zoom und Pan Variablen
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.original_pixmap = None
        self.is_zoomed = False
        self.is_panning = False
        self.pan_start = QPointF(0, 0)
        
        # Touch-Gesten Variablen
        self.touch_points = {}  # Dictionary für Touch-Punkte
        self.initial_distance = 0.0
        self.initial_zoom = 1.0
        self.initial_pan = QPointF(0, 0)
        
        # Pause-Variablen
        self.is_paused = False
        self.info_bar_visible = False
        self.tap_timer = QTimer()
        self.tap_timer.setSingleShot(True)
        self.tap_timer.timeout.connect(self.on_tap_timeout)
        
        # Fade-Animation Variablen
        self.fade_animation = None
        self.is_fading = False
        self.next_pixmap = None  # Nächstes Bild für Fade-Übergang
        
        # Metadaten-Pfad
        self.metadata_file = Path(self.config.get('paths.proxy_images')) / 'metadata.json'
        
        # Touch-Events aktivieren (Multi-Touch Support)
        # WA_AcceptTouchEvents ermöglicht sowohl Touch- als auch Mouse-Events
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        
        self.setup_ui()
        # NICHT hier das erste Bild laden - wird später in _initialize_slideshow() geladen
        # Das verhindert Blockierung beim Start
        # self.load_current_image(use_fade=False)  # Wird verzögert geladen
        
        self.setup_timer()
        self.touch_start_x = None
        self.long_press_timer = QTimer()
        self.long_press_timer.setSingleShot(True)
        self.long_press_timer.timeout.connect(self.on_long_press)
        # Verbinde Signal für thread-sichere Aktualisierung
        self.refresh_requested.connect(self.refresh)
    
    def setup_ui(self):
        """Erstellt die UI-Elemente"""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  # Kein Abstand zwischen Elementen
        
        # Bild-Label Container für Fade-Übergänge
        self.image_container = QWidget()
        image_container_layout = QVBoxLayout()
        image_container_layout.setContentsMargins(0, 0, 0, 0)
        image_container_layout.setSpacing(0)
        
        # Aktuelles Bild-Label (füllt den gesamten Bildschirm)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black;")
        self.image_label.setScaledContents(False)  # Wichtig für Zoom: nicht automatisch skalieren
        image_container_layout.addWidget(self.image_label)
        
        # Nächstes Bild-Label für Fade-Übergang (überlagert, absolut positioniert)
        self.next_image_label = QLabel(self.image_container)
        self.next_image_label.setAlignment(Qt.AlignCenter)
        self.next_image_label.setStyleSheet("background-color: black;")
        self.next_image_label.setScaledContents(False)
        self.next_image_label.hide()  # Standardmäßig versteckt
        # WICHTIG: Events durchlassen, damit Touch/Mouse-Events funktionieren
        self.next_image_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.next_image_label.setAttribute(Qt.WA_NoMousePropagation, True)
        # Positioniere absolut über dem aktuellen Bild
        self.next_image_label.setGeometry(0, 0, 1024, 600)  # Wird in resizeEvent aktualisiert
        
        # Opacity-Effekt für Fade-Animation
        self.next_image_opacity = QGraphicsOpacityEffect()
        self.next_image_opacity.setOpacity(0.0)  # Startet unsichtbar
        self.next_image_label.setGraphicsEffect(self.next_image_opacity)
        
        self.image_container.setLayout(image_container_layout)
        layout.addWidget(self.image_container, 1)  # Stretch-Faktor 1
        
        # Info-Label (klein, unten rechts) - als Overlay
        self.info_label = QLabel(self)
        self.info_label.setStyleSheet("color: white; background-color: rgba(26, 26, 46, 200); padding: 8px; border-radius: 5px;")
        self.info_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.info_label.hide()  # Standardmäßig versteckt
        self.info_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)  # Maus-Events durchlassen
        
        # Info-Bar (unten, dezent) - zeigt Absender und Datum wenn pausiert
        self.info_bar = QWidget(self)
        self.info_bar.setStyleSheet("""
            QWidget {
                background-color: rgba(26, 26, 46, 220);
                border-top: 2px solid rgba(255, 255, 255, 100);
                border-radius: 0px;
            }
        """)
        info_bar_layout = QHBoxLayout()
        info_bar_layout.setContentsMargins(15, 10, 15, 10)
        info_bar_layout.setSpacing(15)
        
        # Pause-Text
        self.pause_text = QLabel("Wiedergabe pausiert")
        self.pause_text.setStyleSheet("color: #ecf0f1; font-size: 16px; font-weight: bold;")
        info_bar_layout.addWidget(self.pause_text)
        
        # Löschen-Button (rechts neben Pause-Text)
        self.delete_button = QPushButton("Bild löschen")
        self.delete_button.setStyleSheet("""
            QPushButton {
                font-size: 14px; font-weight: bold; padding: 8px 15px;
                background: #e74c3c; color: white; border: none;
                border-radius: 8px; min-width: 120px;
            }
            QPushButton:hover {
                background: #c0392b;
            }
            QPushButton:pressed {
                background: #a93226;
            }
        """)
        self.delete_button.clicked.connect(self.delete_current_image)
        info_bar_layout.addWidget(self.delete_button)
        
        info_bar_layout.addStretch()
        
        # Absender
        self.sender_label = QLabel()
        self.sender_label.setStyleSheet("color: #bdc3c7; font-size: 14px;")
        info_bar_layout.addWidget(self.sender_label)
        
        # Betreff
        self.subject_label = QLabel()
        self.subject_label.setStyleSheet("color: #bdc3c7; font-size: 14px;")
        info_bar_layout.addWidget(self.subject_label)
        
        # Ort/Land
        self.location_label = QLabel()
        self.location_label.setStyleSheet("color: #bdc3c7; font-size: 14px;")
        info_bar_layout.addWidget(self.location_label)
        
        # Datum
        self.date_label = QLabel()
        self.date_label.setStyleSheet("color: #bdc3c7; font-size: 14px;")
        info_bar_layout.addWidget(self.date_label)
        
        self.info_bar.setLayout(info_bar_layout)
        self.info_bar.hide()
        # Nur der Löschen-Button soll Maus-Events empfangen, nicht die ganze Info-Bar
        # Die Info-Bar selbst lässt Maus-Events durch, aber der Button nicht
        
        self.setLayout(layout)
        self.load_current_image(use_fade=False)  # Kein Fade beim ersten Laden
    
    def setup_timer(self):
        """Richtet den Timer für automatischen Bildwechsel ein (nur wenn auto_play aktiviert)"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.on_timer_timeout)
        
        # Watchdog-Timer: Prüft alle 60 Sekunden, ob der Haupt-Timer noch läuft
        self.watchdog_timer = QTimer()
        self.watchdog_timer.timeout.connect(self._check_timer_health)
        self.watchdog_timer.start(60000)  # Alle 60 Sekunden prüfen
        
        # Timer nur starten wenn automatische Slideshow aktiviert ist
        auto_play = self.config.get('slideshow.auto_play', True)
        if auto_play:
            interval = self.config.get('slideshow.interval_seconds', 10) * 1000
            self.timer.start(interval)
            logger.info(f"Slideshow-Timer gestartet mit Intervall: {interval/1000}s")
    
    def load_current_image(self, use_fade=True):
        """Lädt das aktuelle Bild aus der Slideshow mit optionalem Fade-Übergang"""
        image_path = self.slideshow.get_current_image()
        if image_path and image_path.exists():
            logger.info(f"Lade Bild: {image_path.name}")
            # Lade Bild (optimiert: verwende load() mit Format für bessere Performance)
            new_pixmap = QPixmap()
            # Lade Bild mit optimierten Optionen (schnelleres Laden)
            if not new_pixmap.load(str(image_path)):
                logger.warning(f"Konnte Bild nicht laden: {image_path}")
                self.show_placeholder()
                return
            
            # Prüfe ob Fade-Übergang verwendet werden soll
            transition_duration = self.config.get('slideshow.transition_duration', 1.0)
            use_fade = use_fade and transition_duration > 0 and not self.is_fading
            
            logger.info(f"Bild geladen: {image_path.name}, use_fade={use_fade}, is_fading={self.is_fading}")
            
            if use_fade and self.original_pixmap and not self.original_pixmap.isNull():
                # Fade-Übergang verwenden
                logger.info(f"Starte Fade-Übergang zu {image_path.name} (Dauer: {transition_duration}s)")
                self._fade_to_new_image(new_pixmap, transition_duration)
            else:
                # Sofortiger Wechsel (kein Fade)
                logger.debug(f"Sofortiger Wechsel zu {image_path.name}")
                self.original_pixmap = new_pixmap
                # Reset Zoom und Pan beim Bildwechsel
                self.reset_zoom()
                self.update_displayed_image()
                self.update_info_label()
                # Verstecke Info-Bar beim Bildwechsel
                if self.info_bar_visible:
                    self.hide_info_bar()
                # Stelle sicher, dass Widget aktualisiert wird
                self.update()
                if QApplication.instance():
                    QApplication.instance().processEvents()
        else:
            # Platzhalter anzeigen
            logger.warning(f"Bild nicht gefunden: {image_path}")
            self.show_placeholder()
    
    def _fade_to_new_image(self, new_pixmap: QPixmap, duration: float):
        """Führt einen Fade-Übergang zum neuen Bild durch"""
        try:
            logger.info(f"_fade_to_new_image aufgerufen, duration={duration}s")
            if self.is_fading:
                # Wenn bereits ein Fade läuft, stoppe ihn und wechsle sofort
                logger.warning("Fade bereits aktiv, stoppe alten Fade und wechsle sofort")
                if self.fade_animation:
                    self.fade_animation.stop()
                    self.fade_animation.deleteLater()
                self.is_fading = False
            
            if new_pixmap.isNull():
                logger.warning("Fade: Neues Pixmap ist ungültig")
                return
            
            self.is_fading = True
            self.next_pixmap = new_pixmap
            logger.info("Fade: next_pixmap gesetzt, bereite Bild vor...")
            
            # Bereite nächstes Bild vor
            self._prepare_next_image(new_pixmap)
            
            # Positioniere nächstes Bild-Label absolut über dem Container
            if self.image_container:
                container_rect = self.image_container.geometry()
                if container_rect.width() > 0 and container_rect.height() > 0:
                    self.next_image_label.setGeometry(0, 0, container_rect.width(), container_rect.height())
                else:
                    # Fallback: verwende Widget-Größe
                    widget_size = self.size()
                    self.next_image_label.setGeometry(0, 0, widget_size.width(), widget_size.height())
            
            # Zeige nächstes Bild-Label (noch unsichtbar)
            self.next_image_label.show()
            self.next_image_label.raise_()
            
            # Erstelle Fade-Animation
            self.fade_animation = QPropertyAnimation(self.next_image_opacity, b"opacity")
            self.fade_animation.setDuration(int(duration * 1000))  # Konvertiere Sekunden zu Millisekunden
            self.fade_animation.setStartValue(0.0)
            self.fade_animation.setEndValue(1.0)
            self.fade_animation.setEasingCurve(QEasingCurve.InOutQuad)
            
            # Wenn Animation fertig ist, wechsle die Labels
            self.fade_animation.finished.connect(self._on_fade_finished)
            
            # Starte Animation
            logger.info(f"Fade: Starte Animation (Dauer: {duration}s)")
            self.fade_animation.start()
            logger.info("Fade: Animation gestartet")
        except Exception as e:
            logger.error(f"Fehler beim Starten des Fade-Übergangs: {e}", exc_info=True)
            # Fallback: sofortiger Wechsel
            self.is_fading = False
            if new_pixmap and not new_pixmap.isNull():
                self.original_pixmap = new_pixmap
                self.reset_zoom()
                self.update_displayed_image()
                self.update_info_label()
    
    def _prepare_next_image(self, pixmap: QPixmap):
        """Bereitet das nächste Bild für den Fade-Übergang vor"""
        if pixmap.isNull():
            return
        
        # Widget-Größe (verwende image_container Größe)
        if self.image_container:
            widget_size = self.image_container.size()
        else:
            widget_size = self.size()
        
        if widget_size.width() <= 0 or widget_size.height() <= 0:
            widget_size = self.size()
        
        # Skaliertes Pixmap erstellen (ohne Zoom für nächstes Bild)
        scaled_pixmap = pixmap.scaled(
            widget_size.width(),
            widget_size.height(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        # Pixmap für Anzeige erstellen (Widget-Größe)
        display_pixmap = QPixmap(widget_size)
        display_pixmap.fill(QColor(0, 0, 0))  # Schwarzer Hintergrund
        
        painter = QPainter(display_pixmap)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # Berechne Position für zentriertes Bild
        x = (widget_size.width() - scaled_pixmap.width()) / 2
        y = (widget_size.height() - scaled_pixmap.height()) / 2
        
        # Zeichne das skalierte Bild
        painter.drawPixmap(int(x), int(y), scaled_pixmap)
        painter.end()
        
        self.next_image_label.setPixmap(display_pixmap)
    
    def _on_fade_finished(self):
        """Wird aufgerufen, wenn Fade-Animation abgeschlossen ist"""
        try:
            logger.info("Fade-Animation abgeschlossen - wechsle Bild")
            # Wechsle die Labels: nächstes Bild wird aktuelles Bild
            if self.next_pixmap and not self.next_pixmap.isNull():
                logger.info(f"Fade: Wechsle zu next_pixmap (vorher: {self.original_pixmap is not None})")
                self.original_pixmap = self.next_pixmap
                self.reset_zoom()
                self.update_displayed_image()
                self.update_info_label()
                logger.info("Bild nach Fade aktualisiert")
            else:
                logger.warning("Fade: next_pixmap ist None oder ungültig")
            
            # Verstecke nächstes Bild-Label
            self.next_image_label.hide()
            self.next_image_opacity.setOpacity(0.0)
            
            # Verstecke Info-Bar beim Bildwechsel
            if self.info_bar_visible:
                self.hide_info_bar()
            
            # Stelle sicher, dass Widget aktualisiert wird
            self.update()
            if QApplication.instance():
                QApplication.instance().processEvents()
        except Exception as e:
            logger.error(f"Fehler beim Abschließen des Fade-Übergangs: {e}", exc_info=True)
        finally:
            self.is_fading = False
            if self.fade_animation:
                self.fade_animation = None
            self.next_pixmap = None
    
    def get_image_metadata(self, image_hash: str) -> dict:
        """Lädt Metadaten für ein Bild"""
        if not self.metadata_file.exists():
            return {}
        
        try:
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
                return metadata.get(image_hash, {})
        except Exception as e:
            logger.error(f"Fehler beim Laden der Metadaten: {e}")
            return {}
    
    def get_exif_date(self, image_path: Path) -> Optional[str]:
        """Extrahiert das Erstellungsdatum aus EXIF-Daten des Originalbilds"""
        try:
            # Finde das Originalbild basierend auf dem Hash
            original_dir = Path(self.config.get('paths.original_images'))
            image_hash = image_path.stem
            
            # Suche nach Originalbild mit gleichem Hash
            for orig_file in original_dir.glob('*'):
                if orig_file.is_file():
                    try:
                        from image_processor import ImageProcessor
                        processor = ImageProcessor()
                        orig_hash = processor._get_file_hash(orig_file)
                        if orig_hash == image_hash:
                            # Originalbild gefunden, EXIF-Daten extrahieren
                            with Image.open(orig_file) as img:
                                exif = img.getexif()
                                if exif is not None:
                                    # 1. Prüfe Haupt-IFD (Tag 306: DateTime)
                                    if 306 in exif:
                                        try:
                                            value = exif[306]
                                            date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                                            return date_obj.isoformat()
                                        except Exception:
                                            # DateTime konnte nicht geparst werden
                                            pass
                                    
                                    # 2. Prüfe erweiterte EXIF-Daten (ExifIFD)
                                    if 34665 in exif:  # ExifOffset
                                        try:
                                            exif_ifd = exif.get_ifd(34665)
                                            # DateTimeOriginal (Tag 36867) hat Priorität
                                            if 36867 in exif_ifd:
                                                try:
                                                    value = exif_ifd[36867]
                                                    date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                                                    return date_obj.isoformat()
                                                except Exception:
                                                    # DateTimeOriginal konnte nicht geparst werden
                                                    pass
                                            # DateTimeDigitized (Tag 36868) als Fallback
                                            elif 36868 in exif_ifd:
                                                try:
                                                    value = exif_ifd[36868]
                                                    date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                                                    return date_obj.isoformat()
                                                except Exception:
                                                    # DateTimeDigitized konnte nicht geparst werden
                                                    pass
                                        except Exception:
                                            # Erweiterte EXIF-Daten konnten nicht gelesen werden
                                            pass
                                    
                                    # 3. Fallback: Suche in allen Tags (für andere Formate)
                                    for tag_id, value in exif.items():
                                        tag = TAGS.get(tag_id, tag_id)
                                        if tag == 'DateTime' and tag_id != 306:  # Falls es woanders ist
                                            try:
                                                if isinstance(value, str):
                                                    date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                                                    return date_obj.isoformat()
                                            except:
                                                pass
                    except Exception as e:
                        # Fehler beim Lesen der Datei, überspringen
                        continue
        except Exception as e:
            # Fehler beim Extrahieren von EXIF-Daten
            pass
        
        # Keine EXIF-Datum-Informationen gefunden
        return None
    
    def show_info_bar(self):
        """Zeigt die Info-Bar mit Metadaten"""
        if not self.info_bar_visible:
            self.info_bar_visible = True
            self.pause_slideshow()
            
            # Lade Metadaten für aktuelles Bild
            image_path = self.slideshow.get_current_image()
            if image_path:
                image_hash = image_path.stem
                metadata = self.get_image_metadata(image_hash)
                
                sender = metadata.get('sender', 'Unbekannt')
                subject = metadata.get('subject', '')
                location = metadata.get('location')  # Stadt (Land)
                
                # Datum: Verwende gespeichertes EXIF-Datum oder Empfangsdatum
                date_str = metadata.get('date', '')
                if date_str:
                    try:
                        date_obj = datetime.fromisoformat(date_str)
                        formatted_date = date_obj.strftime('%d.%m.%Y %H:%M')
                        date_label_text = f"Erstellt: {formatted_date}"
                    except:
                        date_label_text = f"Erstellt: {date_str}"
                else:
                    date_label_text = "Erstellt: Unbekannt"
                
                self.sender_label.setText(f"Von: {sender}")
                if subject:
                    # Kürze Betreff falls zu lang
                    max_length = 40
                    display_subject = subject if len(subject) <= max_length else subject[:max_length-3] + "..."
                    self.subject_label.setText(f"Betreff: {display_subject}")
                    self.subject_label.show()
                else:
                    self.subject_label.hide()
                
                # Ort/Land anzeigen
                if location:
                    self.location_label.setText(f"Ort: {location}")
                    self.location_label.show()
                else:
                    self.location_label.hide()
                
                self.date_label.setText(date_label_text)
            
            # Positioniere Info-Bar unten
            width = self.width()
            height = self.height()
            bar_height = 60
            self.info_bar.setGeometry(0, height - bar_height, width, bar_height)
            self.info_bar.show()
            self.info_bar.raise_()
    
    def delete_current_image(self):
        """Löscht das aktuell angezeigte Bild"""
        image_path = self.slideshow.get_current_image()
        if not image_path or not image_path.exists():
            # Eigener Fehler-Dialog
            error_dialog = QDialog(self)
            error_dialog.setWindowTitle("Fehler")
            error_dialog.setStyleSheet("""
                QDialog {
                    background-color: #1a1a2e;
                }
                QLabel {
                    color: #ecf0f1;
                    font-size: 18px;
                    padding: 20px;
                }
                QPushButton {
                    background-color: #3498db;
                    color: white;
                    padding: 12px 24px;
                    border: none;
                    border-radius: 8px;
                    font-size: 16px;
                    font-weight: bold;
                    min-width: 100px;
                }
                QPushButton:hover {
                    background-color: #2980b9;
                }
            """)
            layout = QVBoxLayout()
            label = QLabel("Kein Bild zum Löschen gefunden.")
            layout.addWidget(label)
            ok_btn = QPushButton("OK")
            ok_btn.clicked.connect(error_dialog.accept)
            layout.addWidget(ok_btn)
            error_dialog.setLayout(layout)
            error_dialog.exec_()
            return
        
        # Eigener Bestätigungsdialog
        confirm_dialog = QDialog(self)
        confirm_dialog.setWindowTitle("Bild löschen")
        confirm_dialog.setStyleSheet("""
            QDialog {
                background-color: #1a1a2e;
            }
            QLabel {
                color: #ecf0f1;
                font-size: 18px;
                padding: 20px;
            }
            QPushButton {
                padding: 12px 24px;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                min-width: 120px;
            }
            QPushButton#delete_btn {
                background-color: #e74c3c;
                color: white;
            }
            QPushButton#delete_btn:hover {
                background-color: #c0392b;
            }
            QPushButton#cancel_btn {
                background-color: #34495e;
                color: white;
            }
            QPushButton#cancel_btn:hover {
                background-color: #2c3e50;
            }
        """)
        layout = QVBoxLayout()
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        label = QLabel("Möchten Sie dieses Bild wirklich löschen?")
        label.setWordWrap(True)
        layout.addWidget(label)
        
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        delete_btn = QPushButton("Löschen")
        delete_btn.setObjectName("delete_btn")
        delete_btn.clicked.connect(confirm_dialog.accept)
        button_layout.addWidget(delete_btn)
        
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.setObjectName("cancel_btn")
        cancel_btn.clicked.connect(confirm_dialog.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        confirm_dialog.setLayout(layout)
        
        # Dialog zentrieren
        if self.main_window:
            parent_geometry = self.main_window.geometry()
            dialog_width = 400
            dialog_height = 150
            x = parent_geometry.x() + (parent_geometry.width() - dialog_width) // 2
            y = parent_geometry.y() + (parent_geometry.height() - dialog_height) // 2
            confirm_dialog.setGeometry(x, y, dialog_width, dialog_height)
        
        if confirm_dialog.exec_() == QDialog.Accepted:
            try:
                proxy_hash = image_path.stem
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                original_dir = Path(self.config.get('paths.original_images'))
                
                # Proxy-Datei löschen
                image_path.unlink()
                logger.info(f"Proxy-Datei gelöscht: {image_path.name}")
                
                # Original-Datei finden und löschen
                original_found = False
                if original_dir.exists():
                    for orig_file in original_dir.rglob("*"):
                        if orig_file.is_file():
                            try:
                                from image_processor import ImageProcessor
                                image_processor = ImageProcessor()
                                orig_hash = image_processor._get_file_hash(orig_file)
                                if orig_hash == proxy_hash:
                                    orig_file.unlink()
                                    logger.info(f"Original-Datei gelöscht: {orig_file.name}")
                                    original_found = True
                                    break
                            except Exception as e:
                                logger.warning(f"Fehler beim Prüfen von {orig_file}: {e}")
                                continue
                
                if not original_found:
                    logger.warning(f"Original-Datei für {image_path.name} nicht gefunden (Hash: {proxy_hash})")
                
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
                    except Exception as e:
                        logger.error(f"Fehler beim Löschen der Metadaten: {e}")
                
                # Playlists aktualisieren
                try:
                    playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                    playlist_manager.remove_image(proxy_hash)
                except Exception as e:
                    logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                
                # Slideshow aktualisieren
                old_index = self.slideshow.current_index
                self.slideshow.refresh()
                
                # Zum nächsten Bild wechseln (oder vorheriges, wenn am Ende)
                if len(self.slideshow.images) > 0:
                    if old_index >= len(self.slideshow.images):
                        self.slideshow.current_index = len(self.slideshow.images) - 1
                    # Lade das neue aktuelle Bild
                    self.load_current_image(use_fade=False)
                else:
                    # Keine Bilder mehr - zeige Platzhalter
                    self.image_label.clear()
                    self.image_label.setText("Keine Bilder vorhanden")
                    self.image_label.setStyleSheet("color: #ecf0f1; font-size: 24px; background-color: #1a1a2e;")
                
                # Info-Bar verstecken nach Löschung
                self.hide_info_bar()
                
                logger.info(f"Bild gelöscht: {image_path.name}")
                
            except Exception as e:
                logger.error(f"Fehler beim Löschen des Bildes: {e}", exc_info=True)
                # Eigener Fehler-Dialog
                error_dialog = QDialog(self)
                error_dialog.setWindowTitle("Fehler")
                error_dialog.setStyleSheet("""
                    QDialog {
                        background-color: #1a1a2e;
                    }
                    QLabel {
                        color: #ecf0f1;
                        font-size: 16px;
                        padding: 20px;
                    }
                    QPushButton {
                        background-color: #e74c3c;
                        color: white;
                        padding: 12px 24px;
                        border: none;
                        border-radius: 8px;
                        font-size: 16px;
                        font-weight: bold;
                        min-width: 100px;
                    }
                    QPushButton:hover {
                        background-color: #c0392b;
                    }
                """)
                layout = QVBoxLayout()
                label = QLabel(f"Fehler beim Löschen des Bildes:\n{str(e)}")
                label.setWordWrap(True)
                layout.addWidget(label)
                ok_btn = QPushButton("OK")
                ok_btn.clicked.connect(error_dialog.accept)
                layout.addWidget(ok_btn)
                error_dialog.setLayout(layout)
                # Dialog zentrieren
                if self.main_window:
                    parent_geometry = self.main_window.geometry()
                    dialog_width = 400
                    dialog_height = 150
                    x = parent_geometry.x() + (parent_geometry.width() - dialog_width) // 2
                    y = parent_geometry.y() + (parent_geometry.height() - dialog_height) // 2
                    error_dialog.setGeometry(x, y, dialog_width, dialog_height)
                error_dialog.exec_()
    
    def hide_info_bar(self):
        """Versteckt die Info-Bar und setzt Wiedergabe fort"""
        if self.info_bar_visible:
            self.info_bar_visible = False
            self.info_bar.hide()
            self.resume_slideshow()
    
    def update_displayed_image(self):
        """Aktualisiert das angezeigte Bild mit Zoom und Pan"""
        if not self.original_pixmap or self.original_pixmap.isNull():
            return
        
        # Widget-Größe
        widget_size = self.image_label.size()
        if widget_size.width() <= 0 or widget_size.height() <= 0:
            return
        
        # Skaliertes Pixmap erstellen
        scaled_pixmap = self.original_pixmap.scaled(
            int(self.original_pixmap.width() * self.zoom_factor),
            int(self.original_pixmap.height() * self.zoom_factor),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        # Pixmap für Anzeige erstellen (Widget-Größe)
        display_pixmap = QPixmap(widget_size)
        display_pixmap.fill(QColor(0, 0, 0))  # Schwarzer Hintergrund
        
        painter = QPainter(display_pixmap)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # Berechne Position für zentriertes Bild mit Pan-Offset
        x = (widget_size.width() - scaled_pixmap.width()) / 2 + self.pan_offset.x()
        y = (widget_size.height() - scaled_pixmap.height()) / 2 + self.pan_offset.y()
        
        # Zeichne das skalierte Bild
        painter.drawPixmap(int(x), int(y), scaled_pixmap)
        painter.end()
        
        logger.debug(f"update_displayed_image: Setze Pixmap auf image_label (Größe: {display_pixmap.width()}x{display_pixmap.height()})")
        self.image_label.setPixmap(display_pixmap)
        # Stelle sicher, dass Label aktualisiert wird
        self.image_label.update()
        self.image_label.repaint()
    
    def show_placeholder(self):
        """Zeigt einen Platzhalter, wenn keine Bilder vorhanden sind"""
        width = self.config.get('display.width', 1024)
        height = self.config.get('display.height', 600)
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(0, 0, 0))
        
        painter = QPainter(pixmap)
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setPointSize(24)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "Keine Bilder vorhanden\n\nBitte Bilder per Email senden")
        painter.end()
        
        self.image_label.setPixmap(pixmap)
        self.info_label.hide()
    
    def update_info_label(self):
        """Aktualisiert das Info-Label mit Bildnummer"""
        current = self.slideshow.current_index + 1
        total = self.slideshow.get_image_count()
        if total > 0:
            self.info_label.setText(f"Bild {current} von {total}")
            self.info_label.show()
            # Position aktualisieren
            width = self.width()
            height = self.height()
            if width > 0 and height > 0:
                info_width = 200
                info_height = 40
                self.info_label.setGeometry(width - info_width - 10, height - info_height - 10, info_width, info_height)
        else:
            self.info_label.hide()
    
    def on_timer_timeout(self):
        """Wird aufgerufen, wenn der Timer abläuft"""
        try:
            logger.info("Slideshow-Timer: on_timer_timeout() aufgerufen")
            if not self.is_paused:  # Nur wenn nicht pausiert
                # Prüfe ob Bilder vorhanden sind
                image_count = self.slideshow.get_image_count()
                logger.info(f"Slideshow-Timer: {image_count} Bilder vorhanden, aktueller Index: {self.slideshow.current_index}")
                if image_count == 0:
                    logger.warning("Slideshow-Timer: Keine Bilder vorhanden")
                    return
                
                # Wechsle zum nächsten Bild
                next_path = self.slideshow.next_image()
                logger.info(f"Slideshow-Timer: Nächstes Bild: {next_path.name if next_path else 'None'} (Index: {self.slideshow.current_index})")
                
                if next_path:
                    self.load_current_image(use_fade=True)  # Fade-Übergang bei automatischem Wechsel
                    logger.info(f"Slideshow: Bild gewechselt zu {next_path.name} (Bild {self.slideshow.current_index + 1}/{image_count})")
                else:
                    logger.warning("Slideshow-Timer: next_image() gab None zurück")
            else:
                logger.debug("Slideshow-Timer: Slideshow ist pausiert")
        except Exception as e:
            logger.error(f"Fehler im Slideshow-Timer: {e}", exc_info=True)
            # Versuche Timer neu zu starten
            try:
                auto_play = self.config.get('slideshow.auto_play', True)
                if auto_play and not self.is_paused:
                    interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                    self.timer.start(interval)
                    logger.info("Slideshow-Timer nach Fehler neu gestartet")
            except Exception as e2:
                logger.error(f"Fehler beim Neustarten des Timers: {e2}", exc_info=True)
    
    def _check_timer_health(self):
        """Watchdog: Prüft ob der Slideshow-Timer noch läuft und startet ihn bei Bedarf neu"""
        try:
            auto_play = self.config.get('slideshow.auto_play', True)
            timer_active = self.timer.isActive() if hasattr(self, 'timer') else False
            logger.info(f"Watchdog: Timer-Status - auto_play={auto_play}, is_paused={self.is_paused}, timer_active={timer_active}")
            
            if auto_play and not self.is_paused:
                if not timer_active:
                    logger.warning("Watchdog: Slideshow-Timer war gestoppt, starte neu...")
                    interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                    self.timer.start(interval)
                    logger.info(f"Watchdog: Timer neu gestartet mit Intervall: {interval/1000}s")
                else:
                    logger.debug(f"Watchdog: Timer läuft korrekt (Intervall: {self.timer.interval()/1000}s)")
            else:
                logger.debug(f"Watchdog: Timer sollte nicht laufen (auto_play={auto_play}, is_paused={self.is_paused})")
        except Exception as e:
            logger.error(f"Fehler im Watchdog-Timer: {e}", exc_info=True)
    
    def pause_slideshow(self):
        """Pausiert die Slideshow"""
        if not self.is_paused:
            self.is_paused = True
            self.timer.stop()
            logger.info("Slideshow pausiert")
    
    def resume_slideshow(self):
        """Setzt die Slideshow fort (nur wenn auto_play aktiviert)"""
        if self.is_paused and not self.info_bar_visible:
            self.is_paused = False
            # Reset Zoom und Pan beim Fortsetzen
            self.reset_zoom()
            # Timer nur starten wenn automatische Slideshow aktiviert ist
            auto_play = self.config.get('slideshow.auto_play', True)
            if auto_play:
                interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                self.timer.start(interval)
                logger.info("Slideshow fortgesetzt")
            else:
                logger.info("Slideshow fortgesetzt (aber auto_play ist deaktiviert, kein Timer)")
    
    def reset_zoom(self):
        """Setzt Zoom und Pan zurück"""
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.is_zoomed = False
        self.is_panning = False
        self.touch_points = {}
        self.initial_distance = 0.0
    
    def mousePressEvent(self, event):
        """Erkennt Touch/Maus-Klicks für Navigation"""
        if event.button() == Qt.LeftButton:
            self.touch_start_x = event.x()
            # Starte Timer für langes Drücken (3 Sekunden)
            if self.main_window:
                self.long_press_timer.start(3000)  # 3 Sekunden
            
            # Starte Tap-Timer für kurzen Tap (Pause)
            self.tap_timer.start(300)  # 300ms für kurzen Tap
    
    def mouseDoubleClickEvent(self, event):
        """Erkennt Doppelklick - springt zum ersten Bild der Playlist"""
        if event.button() == Qt.LeftButton:
            # Stoppe alle Timer
            self.long_press_timer.stop()
            self.tap_timer.stop()
            
            # Springe zum ersten Bild (Index 0)
            if self.slideshow.images:
                self.slideshow.current_index = 0
                # Reset Zoom und Pan
                self.reset_zoom()
                # Lade erstes Bild
                self.load_current_image(use_fade=True)
                # Timer neu starten (nur wenn auto_play aktiviert)
                auto_play = self.config.get('slideshow.auto_play', True)
                interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                self.timer.stop()
                if not self.is_paused and auto_play:
                    self.timer.start(interval)
                logger.info("Zum ersten Bild der Playlist gesprungen (Doppelklick)")
    
    def mouseReleaseEvent(self, event):
        """Erkennt Swipe-Gesten"""
        # Stoppe Long-Press-Timer
        self.long_press_timer.stop()
        
        if event.button() == Qt.LeftButton and self.touch_start_x is not None:
            delta_x = event.x() - self.touch_start_x
            threshold = 50  # Mindest-Swipe-Distanz
            
            if abs(delta_x) > threshold:
                # Swipe erkannt - stoppe Tap-Timer und verstecke Info-Bar
                self.tap_timer.stop()
                self.hide_info_bar()
                
                if delta_x > 0:
                    # Swipe nach rechts = vorheriges Bild
                    self.slideshow.previous_image()
                    self.load_current_image(use_fade=True)  # Fade-Übergang bei manuellem Wechsel
                    # Timer mit aktuellem Intervall neu starten (nur wenn auto_play aktiviert)
                    auto_play = self.config.get('slideshow.auto_play', True)
                    interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                    self.timer.stop()
                    if not self.is_paused and auto_play:
                        self.timer.start(interval)
                    self.previous_requested.emit()
                else:
                    # Swipe nach links = nächstes Bild
                    self.slideshow.next_image()
                    self.load_current_image(use_fade=True)  # Fade-Übergang bei manuellem Wechsel
                    # Timer mit aktuellem Intervall neu starten (nur wenn auto_play aktiviert)
                    auto_play = self.config.get('slideshow.auto_play', True)
                    interval = self.config.get('slideshow.interval_seconds', 10) * 1000
                    self.timer.stop()
                    if not self.is_paused and auto_play:
                        self.timer.start(interval)
                    self.next_requested.emit()
            # Wenn kein Swipe, wird Tap-Timer auslösen (falls nicht bereits abgelaufen)
            
            self.touch_start_x = None
    
    def on_tap_timeout(self):
        """Wird aufgerufen bei kurzem Tap (kein Swipe)"""
        # Kurzer Tap ohne Swipe = Info-Bar anzeigen/verstecken
        if self.info_bar_visible:
            self.hide_info_bar()
        else:
            self.show_info_bar()
    
    def touchEvent(self, event: QTouchEvent):
        """Behandelt Multi-Touch-Events für 2-Finger-Zoom (nur wenn pausiert)"""
        # Nur Multi-Touch (2+ Finger) verarbeiten, Single-Touch wird als Mouse-Event behandelt
        touch_count = len(event.touchPoints())
        
        # Wenn nur 1 Finger oder nicht pausiert: Event ignorieren, damit es als Mouse-Event behandelt wird
        if touch_count < 2 or not self.is_paused:
            event.ignore()
            return False
        
        # Nur 2+ Finger-Zoom verarbeiten
        event.accept()
        
        # Multi-Touch-Events werden verarbeitet (keine Debug-Logs in Production)
        
        if event.type() == QEvent.TouchBegin:
            # Speichere alle Touch-Punkte
            for touch_point in event.touchPoints():
                self.touch_points[touch_point.id()] = touch_point.pos()
            
            # Wenn 2 Finger: Initialisiere Zoom
            if len(self.touch_points) == 2:
                points = list(self.touch_points.values())
                self.initial_distance = self._distance(points[0], points[1])
                if self.initial_distance > 0:
                    self.initial_zoom = self.zoom_factor
                    self.initial_pan = QPointF(self.pan_offset)
                    self.is_zoomed = True
        
        elif event.type() == QEvent.TouchUpdate:
            # Aktualisiere Touch-Punkte
            for touch_point in event.touchPoints():
                if touch_point.state() == Qt.TouchPointMoved:
                    self.touch_points[touch_point.id()] = touch_point.pos()
            
            # Wenn 2 Finger: Berechne Zoom
            if len(self.touch_points) == 2:
                points = list(self.touch_points.values())
                current_distance = self._distance(points[0], points[1])
                
                if self.initial_distance > 0:
                    # Berechne Zoom-Faktor
                    scale = current_distance / self.initial_distance
                    self.zoom_factor = max(1.0, min(5.0, self.initial_zoom * scale))  # Limit: 1x bis 5x
                    
                    # Berechne Mittelpunkt für Zoom
                    center = QPointF(
                        (points[0].x() + points[1].x()) / 2,
                        (points[0].y() + points[1].y()) / 2
                    )
                    
                    # Aktualisiere Pan-Offset basierend auf Zoom-Zentrum
                    widget_center = QPointF(self.width() / 2, self.height() / 2)
                    offset = center - widget_center
                    self.pan_offset = self.initial_pan + offset * (1 - 1/scale)
                    
                    self.update_displayed_image()
            
            # Wenn 1 Finger: Pan (Verschieben) - nur wenn bereits gezoomt
            elif len(self.touch_points) == 1 and self.is_zoomed:
                points = list(self.touch_points.values())
                if not self.is_panning:
                    self.is_panning = True
                    self.pan_start = points[0]
                else:
                    delta = points[0] - self.pan_start
                    self.pan_offset += delta
                    self.pan_start = points[0]
                    self.update_displayed_image()
        
        elif event.type() == QEvent.TouchEnd:
            # Entferne beendete Touch-Punkte
            for touch_point in event.touchPoints():
                if touch_point.state() == Qt.TouchPointReleased:
                    if touch_point.id() in self.touch_points:
                        del self.touch_points[touch_point.id()]
            
            self.is_panning = False
            
            # Wenn keine Touch-Punkte mehr: Zoom bleibt bestehen (wird beim Fortsetzen zurückgesetzt)
            # (Zoom wird beim Fortsetzen der Slideshow zurückgesetzt)
        
        return True
    
    def _distance(self, p1: QPointF, p2: QPointF) -> float:
        """Berechnet die Distanz zwischen zwei Punkten"""
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        return (dx * dx + dy * dy) ** 0.5
    
    def on_long_press(self):
        """Wird aufgerufen bei langem Drücken (3 Sekunden)"""
        if self.main_window:
            self.main_window.show_menu()
    
    def refresh(self):
        """Aktualisiert die Slideshow"""
        try:
            old_count = self.slideshow.get_image_count()
            self.slideshow.refresh()
            new_count = self.slideshow.get_image_count()
            
            # Reset Zoom beim Refresh
            self.reset_zoom()
            
            # Wenn vorher keine Bilder vorhanden waren und jetzt welche vorhanden sind,
            # stelle sicher dass current_index auf 0 gesetzt ist
            if old_count == 0 and new_count > 0:
                self.slideshow.current_index = 0
                logger.info(f"Slideshow hatte keine Bilder, jetzt {new_count} Bilder - lade erstes Bild")
            
            # Lade aktuelles Bild ASYNCHRON (verhindert UI-Blockierung)
            # Verwende QTimer.singleShot um das Laden im nächsten Event-Loop-Zyklus auszuführen
            if new_count > 0:
                QTimer.singleShot(0, lambda: self.load_current_image(use_fade=False))
        except Exception as e:
            logger.error(f"Fehler beim Refresh der Slideshow: {e}", exc_info=True)
    
    def resizeEvent(self, event):
        """Wird aufgerufen wenn Widget-Größe sich ändert"""
        super().resizeEvent(event)
        width = event.size().width()
        height = event.size().height()
        
        # Info-Label positionieren (unten rechts)
        if self.info_label.isVisible():
            info_width = 200
            info_height = 40
            self.info_label.setGeometry(width - info_width - 10, height - info_height - 10, info_width, info_height)
        
        # Info-Bar positionieren (unten)
        if self.info_bar.isVisible():
            bar_height = 60
            self.info_bar.setGeometry(0, height - bar_height, width, bar_height)
        
        # Nächstes Bild-Label positionieren (absolut über aktuelles Bild)
        if self.image_container:
            container_rect = self.image_container.geometry()
            self.next_image_label.setGeometry(0, 0, container_rect.width(), container_rect.height())
        
        # Aktualisiere Bild-Anzeige bei Größenänderung
        if self.original_pixmap:
            self.update_displayed_image()
        # Aktualisiere auch nächstes Bild-Label falls sichtbar
        if self.next_image_label.isVisible() and self.next_pixmap:
            self._prepare_next_image(self.next_pixmap)
        # Timer-Intervall aktualisieren (nur wenn automatische Slideshow aktiviert)
        auto_play = self.config.get('slideshow.auto_play', True)
        interval = self.config.get('slideshow.interval_seconds', 10) * 1000
        was_active = self.timer.isActive()
        self.timer.stop()  # Immer erst stoppen
        self.timer.setInterval(interval)
        # Timer NUR starten wenn auto_play aktiviert ist (unabhängig von was_active)
        if auto_play:
            self.timer.start(interval)
        # Wenn auto_play deaktiviert ist, bleibt Timer gestoppt (kein else nötig, da bereits gestoppt)
    
    def safe_refresh(self):
        """Thread-sichere Aktualisierung der Slideshow"""
        # Verwende Signal für Thread-sicherheit (wird im Haupt-Thread ausgeführt)
        self.refresh_requested.emit()

class ImageManagementWidget(QWidget):
    """Bildverwaltungs-Widget"""
    def __init__(self, config: ConfigManager, image_processor: ImageProcessor):
        super().__init__()
        self.config = config
        self.image_processor = image_processor
        self._initialized = False
        self.setup_ui()
        # NICHT hier refresh_list() aufrufen - wird lazy initialisiert beim ersten Anzeigen
        # self.refresh_list()  # Wird verzögert geladen
    
    def showEvent(self, event):
        """Wird aufgerufen wenn Widget angezeigt wird - lazy initialization"""
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self.refresh_list()
    
    def setup_ui(self):
        """Erstellt die UI-Elemente"""
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        self.setStyleSheet("background-color: #1a1a2e;")  # Gleiches Design wie Menü
        
        # Titel
        title = QLabel("Bildverwaltung")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #ffffff; padding: 10px;")
        layout.addWidget(title)
        
        # Button-Leiste
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        self.delete_btn = QPushButton("Löschen")
        self.delete_btn.setStyleSheet("font-size: 20px; font-weight: bold; padding: 15px; background: #e74c3c; color: white; border: none; border-radius: 10px;")
        self.delete_btn.clicked.connect(self.delete_selected)
        button_layout.addWidget(self.delete_btn)
        
        self.refresh_btn = QPushButton("Aktualisieren")
        self.refresh_btn.setStyleSheet("font-size: 20px; font-weight: bold; padding: 15px; background: #3498db; color: white; border: none; border-radius: 10px;")
        self.refresh_btn.clicked.connect(self.refresh_list)
        button_layout.addWidget(self.refresh_btn)
        
        layout.addLayout(button_layout)
        
        # Bildliste
        self.image_list = QListWidget()
        self.image_list.setStyleSheet("font-size: 18px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px; padding: 10px;")
        layout.addWidget(self.image_list)
        
        # Zurück-Button
        back_btn = QPushButton("Zurück zur Slideshow")
        back_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 20px; background: #95a5a6; color: white; border: none; border-radius: 12px;")
        back_btn.clicked.connect(self.go_back)
        layout.addWidget(back_btn)
        
        self.setLayout(layout)
    
    def refresh_list(self):
        """Aktualisiert die Bildliste"""
        self.image_list.clear()
        proxy_dir = Path(self.config.get('paths.proxy_images'))
        original_dir = Path(self.config.get('paths.original_images'))
        
        for proxy_file in sorted(proxy_dir.glob("*.jpg")):
            # Entsprechendes Original finden
            original_file = None
            for orig_file in original_dir.rglob("*"):
                if orig_file.is_file() and self.image_processor._get_file_hash(orig_file) == proxy_file.stem:
                    original_file = orig_file
                    break
            
            if original_file:
                display_name = f"{original_file.name} ({proxy_file.name})"
            else:
                display_name = proxy_file.name
            
            self.image_list.addItem(display_name)
            self.image_list.item(self.image_list.count() - 1).setData(Qt.UserRole, str(proxy_file))
    
    def delete_selected(self):
        """Löscht das ausgewählte Bild"""
        current_item = self.image_list.currentItem()
        if not current_item:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Keine Auswahl")
            msg.setText("Bitte wählen Sie ein Bild aus.")
            msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                             "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                             "QPushButton { background-color: #f39c12; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                             "QPushButton:hover { background-color: #e67e22; }")
            msg.exec_()
            return
        
        reply = QMessageBox.question(
            self, "Bild löschen",
            "Möchten Sie dieses Bild wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            proxy_path = Path(current_item.data(Qt.UserRole))
            try:
                proxy_hash = proxy_path.stem
                proxy_path.unlink()
                # Original auch löschen, falls gefunden
                original_dir = Path(self.config.get('paths.original_images'))
                for orig_file in original_dir.rglob("*"):
                    if orig_file.is_file() and self.image_processor._get_file_hash(orig_file) == proxy_hash:
                        orig_file.unlink()
                        break
                
                # Metadaten löschen
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                metadata_file = proxy_dir / 'metadata.json'
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        if proxy_hash in metadata:
                            del metadata[proxy_hash]
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        logger.error(f"Fehler beim Löschen der Metadaten: {e}")
                
                # Playlists aktualisieren
                try:
                    playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                    playlist_manager.remove_image(proxy_hash)
                except Exception as e:
                    logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                
                self.refresh_list()
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Information)
                msg.setWindowTitle("Erfolg")
                msg.setText("Bild wurde gelöscht.")
                msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                                 "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                                 "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                                 "QPushButton:hover { background-color: #2980b9; }")
                msg.exec_()
            except Exception as e:
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Critical)
                msg.setWindowTitle("Fehler")
                msg.setText(f"Fehler beim Löschen: {e}")
                msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                                 "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                                 "QPushButton { background-color: #e74c3c; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                                 "QPushButton:hover { background-color: #c0392b; }")
                msg.exec_()
    
    def go_back(self):
        """Signalisiert, zurück zur Slideshow zu gehen"""
        self.parent().setCurrentIndex(0)

class WifiSettingsWidget(QWidget):
    """WLAN-Einstellungs-Widget mit Netzwerkliste"""
    # Signal für UI-Updates aus Threads
    networks_found = pyqtSignal(list)
    scan_error = pyqtSignal(str)
    connection_success = pyqtSignal()
    connection_error = pyqtSignal(str)
    
    def __init__(self, config: ConfigManager, main_window):
        super().__init__()
        self.config = config
        self.main_window = main_window
        self.current_input = None
        self.selected_ssid = None
        self.password_dialog = None
        self.connecting_label = None
        # Verbinde Signale
        self.networks_found.connect(self.display_networks)
        self.scan_error.connect(self.show_scan_error)
        self.connection_success.connect(self.show_connection_success)
        self.connection_error.connect(self.show_connection_error)
        self.setup_ui()
        self.scan_networks()
    
    def setup_ui(self):
        """Erstellt die UI mit Netzwerkliste"""
        # Hintergrund ZUERST setzen, bevor Layout erstellt wird
        self.setStyleSheet("background-color: #1a1a2e;")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#1a1a2e"))
        palette.setColor(self.foregroundRole(), QColor("#ffffff"))
        self.setPalette(palette)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(10)
        
        # Header mit Zurück-Button (wie im Menü)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("WLAN-Einstellungen")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ffffff; padding: 5px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        back_btn = QPushButton("X")
        back_btn.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background: #e74c3c; border: none; border-radius: 18px; min-width: 45px; min-height: 45px;")
        back_btn.clicked.connect(self.go_back)
        header_layout.addWidget(back_btn)
        layout.addLayout(header_layout)
        
        # Aktuelles Netzwerk (kompakt)
        current_label = QLabel("Verfügbare Netzwerke:")
        current_label.setStyleSheet("font-size: 18px; color: #ecf0f1; padding: 5px 0;")
        layout.addWidget(current_label)
        
        # Aktualisieren-Button
        refresh_btn = QPushButton("Aktualisieren")
        refresh_btn.setStyleSheet("font-size: 18px; font-weight: bold; padding: 12px; background: #3498db; color: white; border: none; border-radius: 10px;")
        refresh_btn.clicked.connect(self.scan_networks)
        layout.addWidget(refresh_btn)
        
        # Netzwerkliste (ScrollArea)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #1a1a2e; border: none; } "
                            "QScrollBar:vertical { background: #2c3e50; width: 12px; border-radius: 6px; } "
                            "QScrollBar::handle:vertical { background: #34495e; border-radius: 6px; min-height: 25px; } "
                            "QScrollBar::handle:vertical:hover { background: #3498db; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #1a1a2e;")
        scroll_content.setAutoFillBackground(True)
        palette = scroll_content.palette()
        palette.setColor(scroll_content.backgroundRole(), QColor("#1a1a2e"))
        scroll_content.setPalette(palette)
        self.network_layout = QVBoxLayout()
        self.network_layout.setSpacing(8)
        scroll_content.setLayout(self.network_layout)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        self.setLayout(layout)
        
        # Touch-Tastatur-Widget
        self.keyboard_widget = None
    
    def scan_networks(self):
        """Scannt nach verfügbaren WLAN-Netzwerken und lädt bekannte Netzwerke"""
        # Lösche alte Netzwerke
        while self.network_layout.count():
            child = self.network_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        # Zeige Ladeanzeige
        loading_label = QLabel("Suche nach Netzwerken...")
        loading_label.setStyleSheet("font-size: 18px; color: #bdc3c7; padding: 20px; text-align: center;")
        loading_label.setAlignment(Qt.AlignCenter)
        self.network_layout.addWidget(loading_label)
        self.loading_label = loading_label
        
        # Aktualisiere UI sofort
        QApplication.processEvents()
        
        # Scanne Netzwerke in separatem Thread (um UI nicht zu blockieren)
        def scan():
            try:
                logger.info("Starte WLAN-Scan...")
                
                # Lade bekannte Netzwerke (bereits konfigurierte)
                known_networks = set()
                try:
                    logger.info("Lade bekannte Netzwerke...")
                    result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show'], 
                                          capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if ':802-11-wireless' in line or ':wifi' in line:
                                parts = line.split(':')
                                if len(parts) > 0:
                                    conn_name = parts[0]
                                    known_networks.add(conn_name)
                        logger.info(f"Bekannte Netzwerke gefunden: {len(known_networks)}")
                except Exception as e:
                    logger.warning(f"Fehler beim Laden bekannter Netzwerke: {e}")
                
                # Starte einen aktiven Scan (rescan) um alle verfügbaren Netzwerke zu finden
                logger.info("Starte aktiven WLAN-Rescan...")
                import time
                scan_success = False
                
                # Methode 1: Versuche sudo nmcli rescan (funktioniert ohne Passwort)
                try:
                    rescan_result = subprocess.run(['sudo', '-n', 'nmcli', 'dev', 'wifi', 'rescan'], 
                                                  capture_output=True, text=True, timeout=5)
                    if rescan_result.returncode == 0:
                        logger.info("Rescan erfolgreich (sudo)")
                        scan_success = True
                        time.sleep(3)  # Warte auf Scan-Abschluss
                    else:
                        # Rescan fehlgeschlagen, fortfahren ohne sudo
                        pass
                except Exception:
                    # Rescan fehlgeschlagen, fortfahren
                    pass
                
                # Methode 2: Versuche nmcli scan (ohne sudo, kann funktionieren)
                if not scan_success:
                    try:
                        scan_result = subprocess.run(['nmcli', 'dev', 'wifi', 'scan'], 
                                                    capture_output=True, text=True, timeout=5)
                        if scan_result.returncode == 0:
                            logger.info("Scan erfolgreich (ohne sudo)")
                            scan_success = True
                            time.sleep(2)
                        else:
                            # Scan-Befehl fehlgeschlagen
                            pass
                    except Exception:
                        # Scan-Befehl fehlgeschlagen, fortfahren
                        pass
                
                if not scan_success:
                    logger.warning("Aktiver Scan nicht möglich, verwende gecachte Netzwerkliste")
                
                # Scanne nach WLAN-Netzwerken
                logger.info("Führe nmcli wifi list aus...")
                result = subprocess.run(['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'], 
                                      capture_output=True, text=True, timeout=15)
                
                logger.info(f"nmcli returncode: {result.returncode}")
                logger.info(f"nmcli stdout: {result.stdout[:200]}")
                if result.stderr:
                    logger.warning(f"nmcli stderr: {result.stderr[:200]}")
                
                networks = []
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    logger.info(f"Gefundene Zeilen: {len(lines)}")
                    for line in lines:
                        if line.strip():
                            parts = line.split(':')
                            if len(parts) >= 2:
                                ssid = parts[0] if parts[0] else "Verstecktes Netzwerk"
                                signal = parts[1] if len(parts) > 1 else "0"
                                security = parts[2] if len(parts) > 2 else ""
                                if ssid and ssid != "--":  # Ignoriere leere SSIDs und "--"
                                    try:
                                        signal_int = int(signal) if signal.isdigit() else 0
                                        # Prüfe ob Netzwerk bekannt ist
                                        is_known = ssid in known_networks
                                        networks.append({
                                            'ssid': ssid,
                                            'signal': signal,
                                            'security': security,
                                            'known': is_known
                                        })
                                    except:
                                        pass
                
                logger.info(f"Gefundene Netzwerke: {len(networks)}")
                
                # Sortiere: Bekannte zuerst, dann nach Signalstärke
                networks.sort(key=lambda x: (
                    not x['known'],  # Bekannte zuerst (False < True)
                    -int(x['signal']) if x['signal'].isdigit() else 0  # Dann nach Signalstärke
                ))
                
                # Entferne Duplikate (behalte nur die beste Signalstärke)
                seen = set()
                unique_networks = []
                for net in networks:
                    if net['ssid'] not in seen:
                        seen.add(net['ssid'])
                        unique_networks.append(net)
                
                logger.info(f"Eindeutige Netzwerke: {len(unique_networks)} (davon {sum(1 for n in unique_networks if n['known'])} bekannt)")
                
                # Aktualisiere UI über Signal (thread-safe)
                self.networks_found.emit(unique_networks)
            except subprocess.TimeoutExpired:
                logger.error("WLAN-Scan Timeout")
                self.scan_error.emit("Scan-Timeout. Bitte erneut versuchen.")
            except Exception as e:
                logger.error(f"Fehler beim Scannen der Netzwerke: {e}", exc_info=True)
                self.scan_error.emit(f"Fehler: {str(e)}")
        
        # Starte Scan in separatem Thread
        import threading
        scan_thread = threading.Thread(target=scan, daemon=True)
        scan_thread.start()
    
    def display_networks(self, networks):
        """Zeigt die gefundenen Netzwerke an mit Unterscheidung zwischen bekannten und unbekannten"""
        logger.info(f"display_networks aufgerufen mit {len(networks)} Netzwerken")
        
        # Lösche Ladeanzeige
        while self.network_layout.count():
            child = self.network_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        if not networks:
            no_networks = QLabel("Keine Netzwerke gefunden.\nBitte 'Aktualisieren' drücken.")
            no_networks.setStyleSheet("font-size: 18px; color: #bdc3c7; padding: 20px; text-align: center;")
            no_networks.setAlignment(Qt.AlignCenter)
            no_networks.setWordWrap(True)
            self.network_layout.addWidget(no_networks)
            return
        
        # Trenne bekannte und unbekannte Netzwerke
        known_networks = [n for n in networks if n.get('known', False)]
        unknown_networks = [n for n in networks if not n.get('known', False)]
        
        # Zeige bekannte Netzwerke zuerst
        if known_networks:
            known_label = QLabel("Bekannte Netzwerke:")
            known_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #2ecc71; padding: 15px 0 5px 0;")
            self.network_layout.addWidget(known_label)
            
            for network in known_networks:
                ssid = network['ssid']
                signal = network['signal']
                security = network['security']
                
                # Container für Netzwerk-Button und Löschen-Button
                network_container = QHBoxLayout()
                network_container.setSpacing(10)
                
                # Erstelle Netzwerk-Button für bekanntes Netzwerk
                network_btn = QPushButton()
                network_btn.setStyleSheet("""
                    QPushButton {
                        font-size: 18px; 
                        font-weight: bold; 
                        padding: 15px; 
                        background: #27ae60; 
                        color: #ffffff; 
                        border: 2px solid #2ecc71; 
                        border-radius: 10px;
                        text-align: left;
                    }
                    QPushButton:hover {
                        background: #2ecc71;
                        border-color: #27ae60;
                    }
                """)
                
                # Text mit Signalstärke und Sicherheit
                signal_text = f"{signal}%" if signal.isdigit() else "?"
                security_icon = "🔒" if security else "🔓"
                network_btn.setText(f"✓ {security_icon} {ssid} ({signal_text})")
                # Bei bekannten Netzwerken direkt verbinden (ohne Passwort-Dialog)
                network_btn.clicked.connect(lambda checked, s=ssid, sec=security, known=True: self.connect_to_network(s, sec, known))
                network_container.addWidget(network_btn, stretch=1)
                
                # Löschen-Button für bekanntes Netzwerk
                delete_btn = QPushButton("🗑️")
                delete_btn.setStyleSheet("""
                    QPushButton {
                        font-size: 20px; 
                        font-weight: bold; 
                        padding: 15px 20px; 
                        background: #e74c3c; 
                        color: #ffffff; 
                        border: 2px solid #c0392b; 
                        border-radius: 10px;
                        min-width: 60px;
                    }
                    QPushButton:hover {
                        background: #c0392b;
                        border-color: #e74c3c;
                    }
                """)
                delete_btn.setToolTip(f"Netzwerk {ssid} löschen")
                delete_btn.clicked.connect(lambda checked, s=ssid: self.delete_network(s))
                network_container.addWidget(delete_btn)
                
                # Container-Widget erstellen
                container_widget = QWidget()
                container_widget.setLayout(network_container)
                self.network_layout.addWidget(container_widget)
        
        # Zeige unbekannte Netzwerke
        if unknown_networks:
            if known_networks:
                separator = QLabel("")
                separator.setStyleSheet("height: 10px;")
                self.network_layout.addWidget(separator)
            
            unknown_label = QLabel("Verfügbare Netzwerke:")
            unknown_label.setStyleSheet("font-size: 20px; font-weight: bold; color: #ecf0f1; padding: 15px 0 5px 0;")
            self.network_layout.addWidget(unknown_label)
            
            for network in unknown_networks:
                ssid = network['ssid']
                signal = network['signal']
                security = network['security']
                
                # Erstelle Netzwerk-Button für unbekanntes Netzwerk
                network_btn = QPushButton()
                network_btn.setStyleSheet("""
                    QPushButton {
                        font-size: 18px; 
                        font-weight: bold; 
                        padding: 15px; 
                        background: #2c3e50; 
                        color: #ecf0f1; 
                        border: 2px solid #34495e; 
                        border-radius: 10px;
                        text-align: left;
                    }
                    QPushButton:hover {
                        background: #34495e;
                        border-color: #3498db;
                    }
                """)
                
                # Text mit Signalstärke und Sicherheit
                signal_text = f"{signal}%" if signal.isdigit() else "?"
                security_icon = "🔒" if security else "🔓"
                network_btn.setText(f"{security_icon} {ssid} ({signal_text})")
                # Bei unbekannten Netzwerken Passwort-Dialog zeigen
                network_btn.clicked.connect(lambda checked, s=ssid, sec=security, known=False: self.connect_to_network(s, sec, known))
                self.network_layout.addWidget(network_btn)
        
        self.network_layout.addStretch()
    
    def show_scan_error(self, error):
        """Zeigt Fehler beim Scannen"""
        while self.network_layout.count():
            child = self.network_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        error_label = QLabel(f"Fehler beim Scannen:\n{error}")
        error_label.setStyleSheet("font-size: 16px; color: #e74c3c; padding: 20px; text-align: center;")
        error_label.setAlignment(Qt.AlignCenter)
        error_label.setWordWrap(True)
        self.network_layout.addWidget(error_label)
    
    def connect_to_network(self, ssid, security, known=False):
        """Verbindet mit dem ausgewählten Netzwerk"""
        self.selected_ssid = ssid
        
        # Wenn bekanntes Netzwerk: Direkt verbinden (Passwort ist bereits gespeichert)
        if known:
            logger.info(f"Verbinde mit bekanntem Netzwerk: {ssid}")
            self.connect_known_network(ssid)
            return
        
        # Wenn kein Passwort benötigt (offenes Netzwerk)
        if not security or security == "":
            self.connect_with_password("")
            return
        
        # Zeige Passwort-Dialog für unbekannte Netzwerke
        self.show_password_dialog(ssid)
    
    def show_password_dialog(self, ssid):
        """Zeigt Dialog für Passwort-Eingabe"""
        # Lösche alten Dialog falls vorhanden
        if self.password_dialog:
            self.password_dialog.deleteLater()
        
        # Verstecke Tastatur falls noch offen
        self.hide_system_keyboard()
        
        # Erstelle Dialog-Widget - Positioniere oben, damit Tastatur darunter passt
        dialog = QWidget(self)
        dialog.setStyleSheet("background: #2c3e50; border-radius: 12px; padding: 15px; border: 2px solid #34495e;")
        # Dialog oben positionieren, damit Tastatur darunter passt
        dialog.setGeometry(self.width() // 8, 20, self.width() * 3 // 4, 180)
        dialog_layout = QVBoxLayout()
        dialog_layout.setSpacing(15)
        
        # Titel - kompakter
        title = QLabel(f"Passwort für: {ssid}")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ecf0f1; padding: 5px;")
        title.setAlignment(Qt.AlignCenter)
        title.setWordWrap(True)
        dialog_layout.addWidget(title)
        
        # Passwort-Eingabe - kompakter
        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.Password)
        password_input.setStyleSheet("font-size: 18px; padding: 10px; background: #34495e; color: #ecf0f1; border: 2px solid #1a1a2e; border-radius: 8px;")
        password_input.setPlaceholderText("Passwort eingeben")
        
        # Überschreibe mousePressEvent um System-Tastatur zu starten
        def mouse_press_handler(event):
            logger.info("=== Passwort-Feld angeklickt ===")
            # Führe Standard-Event zuerst aus (für Fokus)
            QLineEdit.mousePressEvent(password_input, event)
            # Starte Tastatur nach kurzer Verzögerung
            QTimer.singleShot(200, lambda: self.show_system_keyboard(password_input))
        
        password_input.mousePressEvent = mouse_press_handler
        
        # Auch bei focusInEvent Tastatur öffnen
        def focus_in_handler(event):
            logger.info("=== Passwort-Feld hat Fokus ===")
            QLineEdit.focusInEvent(password_input, event)
            # Starte Tastatur nach kurzer Verzögerung
            QTimer.singleShot(300, lambda: self.show_system_keyboard(password_input))
        
        password_input.focusInEvent = focus_in_handler
        
        # Auch bei mouseReleaseEvent (falls mousePressEvent nicht funktioniert)
        def mouse_release_handler(event):
            logger.info("=== Passwort-Feld losgelassen ===")
            QLineEdit.mouseReleaseEvent(password_input, event)
            QTimer.singleShot(200, lambda: self.show_system_keyboard(password_input))
        
        password_input.mouseReleaseEvent = mouse_release_handler
        
        
        # Button zum Öffnen der Tastatur (neben Passwort-Feld)
        password_container = QHBoxLayout()
        password_container.setSpacing(10)
        password_container.addWidget(password_input)
        
        keyboard_btn = QPushButton("⌨ Tastatur")
        keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        keyboard_btn.setToolTip("Tastatur öffnen")
        keyboard_btn.clicked.connect(lambda: self.show_system_keyboard(password_input))
        password_container.addWidget(keyboard_btn)
        
        dialog_layout.addLayout(password_container)
        
        # Button-Layout
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # Abbrechen
        cancel_btn = QPushButton("Abbrechen")
        cancel_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px; background: #95a5a6; color: white; border: none; border-radius: 8px;")
        def cancel_and_hide_keyboard():
            self.hide_system_keyboard()
            dialog.hide()
        cancel_btn.clicked.connect(cancel_and_hide_keyboard)
        button_layout.addWidget(cancel_btn)
        
        # Verbinden
        connect_btn = QPushButton("Verbinden")
        connect_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px; background: #2ecc71; color: white; border: none; border-radius: 8px;")
        def connect_and_hide_keyboard():
            self.hide_system_keyboard()
            self.connect_with_password(password_input.text(), dialog)
        connect_btn.clicked.connect(connect_and_hide_keyboard)
        button_layout.addWidget(connect_btn)
        
        dialog_layout.addLayout(button_layout)
        dialog.setLayout(dialog_layout)
        dialog.show()
        dialog.raise_()
        
        self.password_dialog = dialog
        self.current_input = password_input
        
        # Setze Fokus auf Passwort-Feld nach kurzer Verzögerung (öffnet Tastatur automatisch)
        QTimer.singleShot(300, lambda: password_input.setFocus())
    
    def connect_known_network(self, ssid):
        """Verbindet mit einem bereits bekannten Netzwerk (ohne Passwort-Eingabe)"""
        # Zeige Verbindungsanzeige
        connecting_label = QLabel(f"Verbinde mit {ssid}...")
        connecting_label.setStyleSheet("font-size: 18px; color: #3498db; padding: 20px; text-align: center;")
        connecting_label.setAlignment(Qt.AlignCenter)
        connecting_label.setWordWrap(True)
        self.network_layout.insertWidget(0, connecting_label)
        self.connecting_label = connecting_label
        
        # Aktualisiere UI sofort
        QApplication.processEvents()
        
        def connect():
            try:
                logger.info(f"Verbinde mit bekanntem Netzwerk: {ssid}")
                
                # Aktiviere die Verbindung direkt (Passwort ist bereits gespeichert)
                cmd = ['sudo', '-n', 'nmcli', 'connection', 'up', ssid]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                logger.info(f"connection up returncode: {result.returncode}")
                if result.stdout:
                    logger.info(f"connection up stdout: {result.stdout[:200]}")
                if result.stderr:
                    logger.warning(f"connection up stderr: {result.stderr[:200]}")
                
                if result.returncode == 0:
                    logger.info(f"Erfolgreich verbunden mit {ssid}")
                    # Stelle sicher, dass Verbindung stabil konfiguriert ist
                    try:
                        # Autoconnect aktivieren mit hoher Priorität
                        subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'modify', ssid, 
                                      'connection.autoconnect', 'yes',
                                      'connection.autoconnect-priority', '10'], 
                                     capture_output=True, text=True, timeout=5)
                        # Power-Management deaktivieren (verhindert Verbindungsabbrüche)
                        subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'modify', ssid, 
                                      'wifi.powersave', '2'], 
                                     capture_output=True, text=True, timeout=5)
                        logger.info(f"Verbindung {ssid} stabil konfiguriert (autoconnect=yes, priority=10, powersave=off)")
                    except Exception as e:
                        logger.warning(f"Konnte Verbindungs-Einstellungen nicht setzen: {e}")
                    
                    # Erfolg über Signal (thread-safe)
                    self.connection_success.emit()
                else:
                    error_msg = result.stderr[:200] if result.stderr else "Unbekannter Fehler"
                    logger.error(f"Verbindung fehlgeschlagen: {error_msg}")
                    self.connection_error.emit(f"Verbindung fehlgeschlagen: {error_msg}")
            except Exception as e:
                logger.error(f"Fehler beim Verbinden: {e}", exc_info=True)
                self.connection_error.emit(f"Fehler: {str(e)}")
        
        # Starte Verbindung in separatem Thread
        import threading
        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()
    
    def show_system_keyboard(self, input_field):
        """Zeigt die eigene Touch-Tastatur"""
        logger.info("=== Touch-Tastatur wird angezeigt ===")
        self.current_input = input_field
        input_field.setFocus()
        
        # Erstelle oder aktualisiere Tastatur-Widget
        if not self.keyboard_widget:
            self.keyboard_widget = TouchKeyboard(self, input_field)
        else:
            self.keyboard_widget.set_input_field(input_field)
        
        # Aktualisiere Position der Tastatur (falls Parent-Größe sich geändert hat)
        parent_height = self.height() if self else 600
        parent_width = self.width() if self else 1024
        keyboard_height = 250  # Weiter reduziert auf 250px
        # Tastatur 40 Pixel nach oben verschoben
        self.keyboard_widget.setGeometry(0, parent_height - keyboard_height - 40, parent_width, keyboard_height)
        
        self.keyboard_widget.show()
        self.keyboard_widget.raise_()
    
    def hide_system_keyboard(self):
        """Versteckt die Touch-Tastatur"""
        if self.keyboard_widget:
            self.keyboard_widget.hide()
    
    def connect_with_password(self, password, dialog=None):
        """Stellt die Verbindung zum Netzwerk her - komplett überarbeitet"""
        if dialog:
            dialog.hide()
        
        if not self.selected_ssid:
            logger.error("Kein SSID ausgewählt")
            return
        
        # Zeige Verbindungsanzeige
        connecting_label = QLabel(f"Verbinde mit {self.selected_ssid}...")
        connecting_label.setStyleSheet("font-size: 18px; color: #3498db; padding: 20px; text-align: center;")
        connecting_label.setAlignment(Qt.AlignCenter)
        connecting_label.setWordWrap(True)
        self.network_layout.insertWidget(0, connecting_label)
        self.connecting_label = connecting_label
        
        # Aktualisiere UI sofort
        QApplication.processEvents()
        
        def connect():
            try:
                logger.info(f"Starte Verbindung zu {self.selected_ssid} (Passwort vorhanden: {bool(password)})")
                
                # Lösche eventuell vorhandene Verbindung mit gleichem Namen
                logger.info("Lösche eventuell vorhandene Verbindung...")
                subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'delete', self.selected_ssid], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                
                if password and password.strip():
                    # Netzwerk mit Passwort - verwende manuelle Verbindungserstellung
                    logger.info("Erstelle WLAN-Verbindung mit Passwort...")
                    
                    # Finde verfügbares WLAN-Interface
                    result = subprocess.run(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device', 'status'], 
                                          capture_output=True, text=True, timeout=5)
                    wifi_device = None
                    if result.returncode == 0:
                        for line in result.stdout.strip().split('\n'):
                            if ':wifi' in line:
                                wifi_device = line.split(':')[0]
                                break
                    
                    # Erstelle Verbindung mit expliziten Parametern für stabile Verbindung
                    # connection.autoconnect=yes sorgt für automatische Verbindung nach Reboot
                    # connection.autoconnect-priority=10 gibt höhere Priorität (wird zuerst verbunden)
                    # wifi.powersave=2 deaktiviert Power-Management (verhindert Verbindungsabbrüche)
                    cmd = ['sudo', '-n', 'nmcli', 'connection', 'add', 
                           'type', 'wifi',
                           'con-name', self.selected_ssid,
                           'ssid', self.selected_ssid,
                           'wifi-sec.key-mgmt', 'wpa-psk',
                           'wifi-sec.psk', password,
                           'connection.autoconnect', 'yes',
                           'connection.autoconnect-priority', '10',
                           'wifi.powersave', '2']
                    
                    if wifi_device:
                        cmd.extend(['ifname', wifi_device])
                    
                    logger.info(f"Erstelle Verbindung: nmcli connection add (SSID: {self.selected_ssid})")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    
                    logger.info(f"connection add returncode: {result.returncode}")
                    if result.stdout:
                        logger.info(f"connection add stdout: {result.stdout[:200]}")
                    if result.stderr:
                        logger.warning(f"connection add stderr: {result.stderr[:200]}")
                    
                    if result.returncode == 0:
                        # Stelle sicher, dass Verbindung stabil konfiguriert ist
                        try:
                            subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'modify', self.selected_ssid, 
                                          'wifi.powersave', '2',
                                          'connection.autoconnect-priority', '10'], 
                                         capture_output=True, text=True, timeout=5)
                        except Exception as e:
                            logger.warning(f"Konnte Verbindungs-Einstellungen nicht optimieren: {e}")
                        
                        # Aktiviere die Verbindung
                        logger.info(f"Aktiviere Verbindung: {self.selected_ssid}")
                        cmd = ['sudo', '-n', 'nmcli', 'connection', 'up', self.selected_ssid]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        
                        logger.info(f"connection up returncode: {result.returncode}")
                        if result.stdout:
                            logger.info(f"connection up stdout: {result.stdout[:200]}")
                        if result.stderr:
                            logger.warning(f"connection up stderr: {result.stderr[:200]}")
                        
                        # Stelle sicher, dass die Verbindung stabil konfiguriert ist
                        try:
                            # Power-Management deaktivieren (verhindert Verbindungsabbrüche)
                            subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'modify', self.selected_ssid, 
                                          'wifi.powersave', '2'], 
                                         capture_output=True, text=True, timeout=5)
                            # Priorität erhöhen (wird bevorzugt verbunden)
                            subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'modify', self.selected_ssid, 
                                          'connection.autoconnect-priority', '10'], 
                                         capture_output=True, text=True, timeout=5)
                            logger.info(f"Verbindung {self.selected_ssid} stabil konfiguriert (powersave=off, priority=10)")
                        except Exception as e:
                            logger.warning(f"Konnte Verbindungs-Einstellungen nicht optimieren: {e}")
                        
                        # Prüfe ob die Verbindung existiert
                        check_cmd = ['nmcli', 'connection', 'show', self.selected_ssid]
                        check_result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=5)
                        if check_result.returncode == 0:
                            logger.info(f"Verbindung {self.selected_ssid} wurde persistent gespeichert")
                        else:
                            logger.warning(f"Verbindung {self.selected_ssid} konnte nicht verifiziert werden")
                    else:
                        # Fallback: Versuche direkte Verbindung
                        logger.info("Fallback: Versuche direkte Verbindung...")
                        cmd = ['sudo', '-n', 'nmcli', 'device', 'wifi', 'connect', self.selected_ssid, 'password', password]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                else:
                    # Offenes Netzwerk ohne Passwort - auch persistent speichern
                    logger.info("Erstelle Verbindung zu offenem Netzwerk...")
                    # Lösche eventuell vorhandene Verbindung
                    subprocess.run(['sudo', '-n', 'nmcli', 'connection', 'delete', self.selected_ssid], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    
                    # Erstelle Verbindung für offenes Netzwerk (ohne Passwort)
                    # wifi.powersave=2 deaktiviert Power-Management (verhindert Verbindungsabbrüche)
                    cmd = ['sudo', '-n', 'nmcli', 'connection', 'add',
                           'type', 'wifi',
                           'con-name', self.selected_ssid,
                           'ssid', self.selected_ssid,
                           'connection.autoconnect', 'yes',
                           'connection.autoconnect-priority', '10',
                           'wifi.powersave', '2']
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                    
                    if result.returncode == 0:
                        # Aktiviere die Verbindung
                        logger.info(f"Aktiviere offene Verbindung: {self.selected_ssid}")
                        cmd = ['sudo', '-n', 'nmcli', 'connection', 'up', self.selected_ssid]
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                
                logger.info(f"Finaler nmcli returncode: {result.returncode}")
                if result.stdout:
                    logger.info(f"Finaler stdout: {result.stdout[:200]}")
                if result.stderr:
                    logger.warning(f"Finaler stderr: {result.stderr[:200]}")
                
                if result.returncode == 0:
                    logger.info(f"Verbindung erfolgreich zu {self.selected_ssid}")
                    # Warte kurz, damit die Verbindung stabilisiert wird
                    import time
                    time.sleep(2)
                    self.connection_success.emit()
                else:
                    error_msg = result.stderr.strip() or result.stdout.strip() or "Verbindung fehlgeschlagen"
                    logger.error(f"Verbindung fehlgeschlagen: {error_msg}")
                    self.connection_error.emit(error_msg)
            except subprocess.TimeoutExpired:
                logger.error("Verbindungs-Timeout")
                self.connection_error.emit("Verbindungs-Timeout. Bitte erneut versuchen.")
            except Exception as e:
                logger.error(f"Fehler beim Verbinden: {e}", exc_info=True)
                self.connection_error.emit(f"Fehler: {str(e)}")
        
        # Starte Verbindung in separatem Thread
        import threading
        connect_thread = threading.Thread(target=connect, daemon=True)
        connect_thread.start()
    
    def show_connection_success(self):
        """Zeigt Erfolgsmeldung"""
        # Entferne Verbindungsanzeige
        if self.connecting_label:
            self.network_layout.removeWidget(self.connecting_label)
            self.connecting_label.deleteLater()
            self.connecting_label = None
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Erfolg")
        msg.setText(f"Verbunden mit {self.selected_ssid}")
        msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                         "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                         "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                         "QPushButton:hover { background-color: #2980b9; }")
        msg.exec_()
        
        # Aktualisiere Netzwerkliste nach kurzer Verzögerung
        QTimer.singleShot(1000, self.scan_networks)
    
    def show_connection_error(self, error):
        """Zeigt Fehlermeldung"""
        # Entferne Verbindungsanzeige
        if self.connecting_label:
            self.network_layout.removeWidget(self.connecting_label)
            self.connecting_label.deleteLater()
            self.connecting_label = None
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Fehler")
        msg.setText(f"Verbindung fehlgeschlagen:\n{error}")
        msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                         "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                         "QPushButton { background-color: #e74c3c; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                         "QPushButton:hover { background-color: #c0392b; }")
        msg.exec_()
    
    def go_back(self):
        """Geht zurück zum Menü und aktualisiert WLAN-Informationen"""
        try:
            # Verstecke System-Tastatur
            self.hide_system_keyboard()
            self.hide()
            self.deleteLater()
            
            # Zeige Slideshow wieder an
            if hasattr(self.main_window, 'slideshow_widget'):
                self.main_window.slideshow_widget.show()
            
            # Zeige Menü wieder an und aktualisiere WLAN-Info (prüfe ob Widget noch existiert)
            if hasattr(self.main_window, 'current_menu') and self.main_window.current_menu:
                try:
                    # Prüfe ob Widget noch existiert (durch Zugriff auf eine Eigenschaft)
                    _ = self.main_window.current_menu.isVisible()
                    # Aktualisiere WLAN-Info-Widget im Menü
                    self.main_window.update_wifi_info_in_menu()
                    self.main_window.current_menu.show()
                    self.main_window.menu_visible = True
                except RuntimeError:
                    # Widget wurde bereits gelöscht, erstelle neues Menü
                    logger.debug("Menü-Widget wurde gelöscht, erstelle neues Menü")
                    self.main_window.current_menu = None
                    self.main_window.menu_visible = False
            logger.info("Zurück zum Menü (von WLAN-Einstellungen) - WLAN-Info aktualisiert")
        except Exception as e:
            logger.error(f"Fehler beim Zurückkehren zum Menü: {e}", exc_info=True)

class TouchKeyboard(QWidget):
    """Vollständige QWERTZ-Touch-Tastatur mit Groß-/Kleinschreibung und Sonderzeichen"""
    def __init__(self, parent, input_field):
        super().__init__(parent)
        self.input_field = input_field
        self.shift_pressed = False
        self.special_mode = False
        self.setup_keyboard()
    
    def set_input_field(self, input_field):
        """Setzt das aktuelle Eingabefeld"""
        self.input_field = input_field
    
    def setup_keyboard(self):
        """Erstellt die vollständige Touch-Tastatur - kompakt für 1024x600 Bildschirm"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)
        
        # QWERTZ Layout (deutsche Tastatur) - kompakt
        # Zeile 1: Zahlen mit Sonderzeichen
        row1 = QHBoxLayout()
        row1.setSpacing(2)
        number_row = [
            ('1', '!'), ('2', '"'), ('3', '§'), ('4', '$'), ('5', '%'),
            ('6', '&'), ('7', '/'), ('8', '('), ('9', ')'), ('0', '=')
        ]
        for normal, shift in number_row:
            btn = self.create_key_button(normal, shift)
            row1.addWidget(btn)
        main_layout.addLayout(row1)
        
        # Zeile 2: QWERTZ...
        row2 = QHBoxLayout()
        row2.setSpacing(2)
        for char in "qwertzuiop":
            btn = self.create_key_button(char, char.upper())
            row2.addWidget(btn)
        # Ü-Taste
        ue_btn = self.create_key_button('ü', 'Ü')
        row2.addWidget(ue_btn)
        # Plus-Taste
        plus_btn = self.create_key_button('+', '*')
        row2.addWidget(plus_btn)
        main_layout.addLayout(row2)
        
        # Zeile 3: ASDF...
        row3 = QHBoxLayout()
        row3.setSpacing(2)
        row3.addStretch()
        for char in "asdfghjkl":
            btn = self.create_key_button(char, char.upper())
            row3.addWidget(btn)
        # Ö-Taste
        oe_btn = self.create_key_button('ö', 'Ö')
        row3.addWidget(oe_btn)
        # Ä-Taste
        ae_btn = self.create_key_button('ä', 'Ä')
        row3.addWidget(ae_btn)
        # Hash-Taste
        hash_btn = self.create_key_button('#', "'")
        row3.addWidget(hash_btn)
        row3.addStretch()
        main_layout.addLayout(row3)
        
        # Zeile 4: YXCV...
        row4 = QHBoxLayout()
        row4.setSpacing(2)
        # Shift-Taste
        shift_btn = QPushButton("SHIFT")
        shift_btn.setCheckable(True)
        shift_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px; font-weight: bold; padding: 8px 12px;
                background: #95a5a6; color: white; border: 2px solid #7f8c8d;
                border-radius: 5px; min-width: 55px; min-height: 35px;
            }
            QPushButton:checked {
                background: #3498db; border-color: #2980b9;
            }
        """)
        shift_btn.clicked.connect(self.toggle_shift)
        row4.addWidget(shift_btn)
        
        for char in "yxcvbnm":
            btn = self.create_key_button(char, char.upper())
            row4.addWidget(btn)
        # Komma und Punkt
        comma_btn = self.create_key_button(',', ';')
        row4.addWidget(comma_btn)
        dot_btn = self.create_key_button('.', ':')
        row4.addWidget(dot_btn)
        # @-Zeichen (wichtig für Email)
        at_btn = self.create_key_button('@', '@')
        row4.addWidget(at_btn)
        # Minus
        minus_btn = self.create_key_button('-', '_')
        row4.addWidget(minus_btn)
        main_layout.addLayout(row4)
        
        # Zeile 5: Funktionstasten
        row5 = QHBoxLayout()
        row5.setSpacing(2)
        # Sonderzeichen-Modus (vorerst deaktiviert)
        special_btn = QPushButton("123")
        special_btn.setCheckable(True)
        special_btn.setStyleSheet("""
            QPushButton {
                font-size: 13px; font-weight: bold; padding: 8px 12px;
                background: #95a5a6; color: white; border: 2px solid #7f8c8d;
                border-radius: 5px; min-width: 55px; min-height: 35px;
            }
            QPushButton:checked {
                background: #9b59b6; border-color: #8e44ad;
            }
        """)
        special_btn.clicked.connect(self.toggle_special)
        row5.addWidget(special_btn)
        
        # Leertaste
        space_btn = QPushButton("Leertaste")
        space_btn.setStyleSheet("font-size: 15px; font-weight: bold; padding: 8px 70px; background: #ecf0f1; color: #2c3e50; border: 2px solid #bdc3c7; border-radius: 5px; min-height: 35px;")
        space_btn.clicked.connect(lambda: self.add_char(" "))
        row5.addWidget(space_btn)
        
        # Backspace
        backspace_btn = QPushButton("Zurueck")
        backspace_btn.setStyleSheet("font-size: 15px; font-weight: bold; padding: 8px 18px; background: #e74c3c; color: white; border: none; border-radius: 5px; min-width: 65px; min-height: 35px;")
        backspace_btn.clicked.connect(self.backspace)
        row5.addWidget(backspace_btn)
        
        # Fertig
        done_btn = QPushButton("Fertig")
        done_btn.setStyleSheet("font-size: 15px; font-weight: bold; padding: 8px 18px; background: #2ecc71; color: white; border: none; border-radius: 5px; min-width: 65px; min-height: 35px;")
        done_btn.clicked.connect(self.hide)
        row5.addWidget(done_btn)
        main_layout.addLayout(row5)
        
        self.setLayout(main_layout)
        self.setStyleSheet("background: #2c3e50; border-radius: 12px; border: 2px solid #34495e;")
        # Positioniere Tastatur am unteren Rand - kompakt für 1024x600 Bildschirm
        parent_height = self.parent().height() if self.parent() else 600
        parent_width = self.parent().width() if self.parent() else 1024
        keyboard_height = 250  # Weiter reduziert auf 250px, damit alle Buttons sichtbar sind
        # Tastatur am unteren Rand, aber 40 Pixel nach oben verschoben
        self.setGeometry(0, parent_height - keyboard_height - 40, parent_width, keyboard_height)
    
    def create_key_button(self, normal_char, shift_char):
        """Erstellt einen Tasten-Button mit normaler und Shift-Variante - kompakt"""
        btn = QPushButton()
        btn.setStyleSheet("""
            QPushButton {
                font-size: 15px; font-weight: bold; padding: 6px;
                background: #ecf0f1; color: #2c3e50;
                border: 2px solid #bdc3c7; border-radius: 5px;
                min-width: 38px; min-height: 35px;
            }
            QPushButton:pressed {
                background: #bdc3c7;
            }
        """)
        
        # Speichere beide Zeichen
        btn.normal_char = normal_char
        btn.shift_char = shift_char
        
        # Setze initialen Text
        self.update_button_text(btn)
        
        btn.clicked.connect(lambda checked, b=btn: self.add_char_from_button(b))
        return btn
    
    def update_button_text(self, btn):
        """Aktualisiert den Text eines Buttons basierend auf Shift-Status"""
        if self.shift_pressed:
            btn.setText(btn.shift_char)
        else:
            btn.setText(btn.normal_char)
    
    def toggle_shift(self):
        """Wechselt zwischen Groß- und Kleinschreibung"""
        self.shift_pressed = not self.shift_pressed
        # Aktualisiere alle Tasten-Buttons (aber nicht Shift-Button selbst)
        for widget in self.findChildren(QPushButton):
            if hasattr(widget, 'normal_char'):
                self.update_button_text(widget)
    
    def toggle_special(self):
        """Wechselt zu Sonderzeichen-Modus (noch nicht implementiert)"""
        self.special_mode = not self.special_mode
    
    def add_char_from_button(self, btn):
        """Fügt das Zeichen vom Button hinzu (berücksichtigt Shift)"""
        if self.shift_pressed:
            char = btn.shift_char
            # Nach Eingabe Shift automatisch zurücksetzen
            self.shift_pressed = False
            # Aktualisiere Shift-Button
            shift_btn = None
            for widget in self.findChildren(QPushButton):
                if widget.isCheckable() and widget.text() == "SHIFT":
                    shift_btn = widget
                    break
            if shift_btn:
                shift_btn.setChecked(False)
            # Aktualisiere alle Tasten-Buttons
            for widget in self.findChildren(QPushButton):
                if hasattr(widget, 'normal_char'):
                    self.update_button_text(widget)
        else:
            char = btn.normal_char
        self.add_char(char)
    
    def add_char(self, char):
        """Fügt ein Zeichen zum Eingabefeld hinzu"""
        if self.input_field:
            # Unterstütze sowohl QLineEdit als auch QTextEdit und QSpinBox
            if isinstance(self.input_field, QLineEdit):
                current_text = self.input_field.text()
                cursor_pos = self.input_field.cursorPosition()
                new_text = current_text[:cursor_pos] + char + current_text[cursor_pos:]
                self.input_field.setText(new_text)
                self.input_field.setCursorPosition(cursor_pos + 1)
            elif isinstance(self.input_field, QTextEdit):
                current_text = self.input_field.toPlainText()
                cursor = self.input_field.textCursor()
                cursor_pos = cursor.position()
                cursor.insertText(char)
            elif isinstance(self.input_field, QSpinBox):
                # Für SpinBox: Konvertiere zu Text, füge Zeichen hinzu, konvertiere zurück
                current_value = self.input_field.value()
                current_text = str(current_value)
                # Füge Zeichen hinzu und versuche zu konvertieren
                try:
                    new_value = int(current_text + char)
                    if self.input_field.minimum() <= new_value <= self.input_field.maximum():
                        self.input_field.setValue(new_value)
                except ValueError:
                    pass  # Ignoriere ungültige Eingaben
    
    def backspace(self):
        """Löscht das letzte Zeichen"""
        if self.input_field:
            if isinstance(self.input_field, QLineEdit):
                current_text = self.input_field.text()
                cursor_pos = self.input_field.cursorPosition()
                if cursor_pos > 0:
                    new_text = current_text[:cursor_pos-1] + current_text[cursor_pos:]
                    self.input_field.setText(new_text)
                    self.input_field.setCursorPosition(cursor_pos - 1)
            elif isinstance(self.input_field, QTextEdit):
                cursor = self.input_field.textCursor()
                if cursor.hasSelection():
                    cursor.removeSelectedText()
                elif cursor.position() > 0:
                    cursor.deletePreviousChar()
            elif isinstance(self.input_field, QSpinBox):
                # Für SpinBox: Reduziere Wert um 1 oder setze auf Minimum
                current_value = self.input_field.value()
                if current_value > self.input_field.minimum():
                    self.input_field.setValue(current_value - 1)
                else:
                    self.input_field.setValue(self.input_field.minimum())

class MenuImageManagementWidget(QWidget):
    """Bildverwaltungs-Widget für das Menü (Grid-Ansicht wie Webinterface)"""
    def __init__(self, config: ConfigManager, image_processor: ImageProcessor, main_window, parent_menu):
        super().__init__()
        self.config = config
        self.image_processor = image_processor
        self.main_window = main_window
        self.parent_menu = parent_menu
        self.setup_ui()
        self.refresh_list()
    
    def setup_ui(self):
        """Erstellt die UI mit Grid-Ansicht"""
        # Hintergrund ZUERST setzen, bevor Layout erstellt wird
        self.setStyleSheet("background-color: #1a1a2e;")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#1a1a2e"))
        palette.setColor(self.foregroundRole(), QColor("#ffffff"))
        self.setPalette(palette)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        # Header mit Zurück-Button (wie im Menü)
        header_layout = QHBoxLayout()
        title = QLabel("Bilder verwalten")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #ffffff; padding: 10px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        back_btn = QPushButton("X")
        back_btn.setStyleSheet("font-size: 20px; font-weight: bold; color: white; background: #e74c3c; border: none; border-radius: 20px; min-width: 50px; min-height: 50px;")
        back_btn.clicked.connect(self.go_back)
        header_layout.addWidget(back_btn)
        layout.addLayout(header_layout)
        
        # ScrollArea für Bild-Grid (wie im Menü)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #1a1a2e; border: none; } "
                            "QScrollBar:vertical { background: #2c3e50; width: 15px; border-radius: 7px; } "
                            "QScrollBar::handle:vertical { background: #34495e; border-radius: 7px; min-height: 30px; } "
                            "QScrollBar::handle:vertical:hover { background: #3498db; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #1a1a2e;")
        scroll_content.setAutoFillBackground(True)
        palette = scroll_content.palette()
        palette.setColor(scroll_content.backgroundRole(), QColor("#1a1a2e"))
        scroll_content.setPalette(palette)
        self.grid_layout = QGridLayout()
        self.grid_layout.setSpacing(15)
        scroll_content.setLayout(self.grid_layout)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        self.setLayout(layout)
    
    def refresh_list(self):
        """Aktualisiert die Bildliste"""
        # Lösche alte Widgets
        while self.grid_layout.count():
            child = self.grid_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        proxy_dir = Path(self.config.get('paths.proxy_images'))
        images = sorted(proxy_dir.glob("*.jpg"))
        
        if not images:
            no_images_label = QLabel("Keine Bilder vorhanden")
            no_images_label.setStyleSheet("font-size: 24px; color: #bdc3c7; padding: 30px; background: #2c3e50; border-radius: 15px; border: 2px solid #34495e;")
            no_images_label.setAlignment(Qt.AlignCenter)
            self.grid_layout.addWidget(no_images_label, 0, 0)
            return
        
        # Erstelle Grid (3 Spalten)
        cols = 3
        for idx, proxy_file in enumerate(images):
            row = idx // cols
            col = idx % cols
            
            # Bild-Container (wie im Menü - einfacher)
            container = QWidget()
            container.setStyleSheet("background: #2c3e50; border-radius: 15px; padding: 15px; margin: 10px; border: 2px solid #34495e;")
            container_layout = QVBoxLayout()
            container_layout.setContentsMargins(8, 8, 8, 8)
            container_layout.setSpacing(10)
            
            # Bild
            image_label = QLabel()
            pixmap = QPixmap(str(proxy_file))
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(200, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                image_label.setPixmap(scaled_pixmap)
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setStyleSheet("background: #1a1a2e; border-radius: 10px; padding: 5px;")
            container_layout.addWidget(image_label)
            
            # Löschen-Button
            delete_btn = QPushButton("Löschen")
            delete_btn.setStyleSheet("font-size: 18px; font-weight: bold; padding: 12px; background: #e74c3c; color: white; border: none; border-radius: 8px;")
            delete_btn.clicked.connect(lambda checked, f=proxy_file: self.delete_image(f))
            container_layout.addWidget(delete_btn)
            
            container.setLayout(container_layout)
            self.grid_layout.addWidget(container, row, col)
    
    def delete_image(self, proxy_file):
        """Löscht ein Bild"""
        reply = QMessageBox.question(
            self, "Bild löschen",
            f"Möchten Sie dieses Bild wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            try:
                proxy_hash = proxy_file.stem
                proxy_file.unlink()
                # Original auch löschen
                original_dir = Path(self.config.get('paths.original_images'))
                for orig_file in original_dir.rglob("*"):
                    if orig_file.is_file():
                        orig_hash = self.image_processor._get_file_hash(orig_file)
                        if orig_hash == proxy_hash:
                            orig_file.unlink()
                            break
                
                # Metadaten löschen
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                metadata_file = proxy_dir / 'metadata.json'
                if metadata_file.exists():
                    try:
                        with open(metadata_file, 'r', encoding='utf-8') as f:
                            metadata = json.load(f)
                        if proxy_hash in metadata:
                            del metadata[proxy_hash]
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        logger.error(f"Fehler beim Löschen der Metadaten: {e}")
                
                # Playlists aktualisieren
                try:
                    playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                    playlist_manager.remove_image(proxy_hash)
                except Exception as e:
                    logger.warning(f"Fehler beim Aktualisieren der Playlists: {e}")
                
                self.refresh_list()
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Information)
                msg.setWindowTitle("Erfolg")
                msg.setText("Bild wurde gelöscht.")
                msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                                 "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                                 "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                                 "QPushButton:hover { background-color: #2980b9; }")
                msg.exec_()
            except Exception as e:
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Critical)
                msg.setWindowTitle("Fehler")
                msg.setText(f"Fehler beim Löschen: {e}")
                msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                                 "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                                 "QPushButton { background-color: #e74c3c; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                                 "QPushButton:hover { background-color: #c0392b; }")
                msg.exec_()
    
    def go_back(self):
        """Geht zurück zum Menü"""
        try:
            self.hide()
            self.deleteLater()
            # Zeige Slideshow wieder an
            if hasattr(self.main_window, 'slideshow_widget'):
                self.main_window.slideshow_widget.show()
            # Zeige Menü wieder an (prüfe ob Widget noch existiert)
            if hasattr(self.main_window, 'current_menu') and self.main_window.current_menu:
                try:
                    # Prüfe ob Widget noch existiert (durch Zugriff auf eine Eigenschaft)
                    _ = self.main_window.current_menu.isVisible()
                    self.main_window.current_menu.show()
                    self.main_window.menu_visible = True
                except RuntimeError:
                    # Widget wurde bereits gelöscht, erstelle neues Menü
                    logger.debug("Menü-Widget wurde gelöscht, erstelle neues Menü")
                    self.main_window.current_menu = None
                    self.main_window.menu_visible = False
            logger.info("Zurück zum Menü")
        except Exception as e:
            logger.error(f"Fehler beim Zurückkehren zum Menü: {e}", exc_info=True)

class SettingsWidget(QWidget):
    """Einstellungs-Widget"""
    # Signal für thread-sichere Email-Test-Updates
    email_test_result = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, config: ConfigManager, main_window=None):
        super().__init__()
        self.config = config
        self.main_window = main_window  # Referenz zu MainWindow für Live-Updates
        self.current_input = None
        self.keyboard_widget = None
        self._initialized = False
        # Verbinde Signal mit Slot
        self.email_test_result.connect(self._on_email_test_result)
        self.setup_ui()
        # NICHT hier load_settings() aufrufen - wird lazy initialisiert beim ersten Anzeigen
        # self.load_settings()  # Wird verzögert geladen
    
    def showEvent(self, event):
        """Wird aufgerufen wenn Widget angezeigt wird - lazy initialization"""
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self.load_settings()
    
    def setup_ui(self):
        """Erstellt die UI-Elemente"""
        # Hintergrund ZUERST setzen, bevor Layout erstellt wird
        self.setStyleSheet("background-color: #1a1a2e;")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#1a1a2e"))
        palette.setColor(self.foregroundRole(), QColor("#ffffff"))
        self.setPalette(palette)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        # Header mit Titel und Zurück-Button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Einstellungen")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ffffff; padding: 5px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        back_btn = QPushButton("X")
        back_btn.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background: #e74c3c; border: none; border-radius: 18px; min-width: 45px; min-height: 45px;")
        back_btn.clicked.connect(self.go_back)
        header_layout.addWidget(back_btn)
        layout.addLayout(header_layout)
        
        # ScrollArea für scrollbare Einstellungen
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #1a1a2e; border: none; } "
                            "QScrollBar:vertical { background: #2c3e50; width: 15px; border-radius: 7px; } "
                            "QScrollBar::handle:vertical { background: #34495e; border-radius: 7px; min-height: 30px; } "
                            "QScrollBar::handle:vertical:hover { background: #3498db; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #1a1a2e;")
        scroll_content.setAutoFillBackground(True)
        palette = scroll_content.palette()
        palette.setColor(scroll_content.backgroundRole(), QColor("#1a1a2e"))
        scroll_content.setPalette(palette)
        scroll_layout = QVBoxLayout()
        scroll_layout.setContentsMargins(10, 10, 10, 10)
        scroll_layout.setSpacing(20)
        
        # Slideshow-Einstellungen (vereinfacht, Touch-optimiert)
        slideshow_group = QLabel("Slideshow")
        slideshow_group.setStyleSheet("font-size: 24px; font-weight: bold; color: #ecf0f1; padding: 10px 0; border-bottom: 2px solid #34495e;")
        scroll_layout.addWidget(slideshow_group)
        
        # Automatische Slideshow
        self.auto_play_check = QCheckBox("Automatische Slideshow")
        self.auto_play_check.setStyleSheet("""
            QCheckBox {
                font-size: 22px; color: #ecf0f1; padding: 15px 0;
                spacing: 15px;
            }
            QCheckBox::indicator {
                width: 35px; height: 35px;
                border: 3px solid #34495e; border-radius: 8px;
                background: #2c3e50;
            }
            QCheckBox::indicator:checked {
                background: #2ecc71; border-color: #27ae60;
            }
        """)
        scroll_layout.addWidget(self.auto_play_check)
        
        # Intervall (Touch-optimiert mit +/- Buttons)
        interval_label = QLabel("Wechsel alle (Sekunden):")
        interval_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(interval_label)
        interval_container = QHBoxLayout()
        interval_container.setSpacing(15)
        
        # Minus-Button
        interval_minus_btn = QPushButton("−")
        interval_minus_btn.setStyleSheet("font-size: 32px; font-weight: bold; padding: 20px 30px; background: #e74c3c; color: white; border: none; border-radius: 12px; min-width: 80px; min-height: 60px;")
        interval_minus_btn.clicked.connect(lambda: self.interval_spin.setValue(max(1, self.interval_spin.value() - 1)))
        interval_container.addWidget(interval_minus_btn)
        
        # Wert-Anzeige (groß, Touch-freundlich)
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 300)
        self.interval_spin.setStyleSheet("font-size: 28px; font-weight: bold; padding: 20px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 12px; text-align: center;")
        self.interval_spin.setButtonSymbols(QSpinBox.NoButtons)  # Keine Standard-Buttons
        interval_container.addWidget(self.interval_spin, stretch=1)
        
        # Plus-Button
        interval_plus_btn = QPushButton("+")
        interval_plus_btn.setStyleSheet("font-size: 32px; font-weight: bold; padding: 20px 30px; background: #2ecc71; color: white; border: none; border-radius: 12px; min-width: 80px; min-height: 60px;")
        interval_plus_btn.clicked.connect(lambda: self.interval_spin.setValue(min(300, self.interval_spin.value() + 1)))
        interval_container.addWidget(interval_plus_btn)
        
        scroll_layout.addLayout(interval_container)
        
        # Fade-Übergang Dauer (Touch-optimiert mit +/- Buttons)
        fade_label = QLabel("Fade-Übergang Dauer (Sekunden, 0 = deaktiviert):")
        fade_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(fade_label)
        fade_container = QHBoxLayout()
        fade_container.setSpacing(15)
        
        # Minus-Button
        fade_minus_btn = QPushButton("−")
        fade_minus_btn.setStyleSheet("font-size: 32px; font-weight: bold; padding: 20px 30px; background: #e74c3c; color: white; border: none; border-radius: 12px; min-width: 80px; min-height: 60px;")
        fade_minus_btn.clicked.connect(lambda: self.fade_duration_spin.setValue(max(0.0, round(self.fade_duration_spin.value() - 0.1, 1))))
        fade_container.addWidget(fade_minus_btn)
        
        # Wert-Anzeige
        self.fade_duration_spin = QDoubleSpinBox()
        self.fade_duration_spin.setRange(0.0, 10.0)
        self.fade_duration_spin.setSuffix(" s")
        self.fade_duration_spin.setSingleStep(0.1)
        self.fade_duration_spin.setDecimals(1)
        self.fade_duration_spin.setStyleSheet("font-size: 28px; font-weight: bold; padding: 20px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 12px; text-align: center;")
        self.fade_duration_spin.setButtonSymbols(QDoubleSpinBox.NoButtons)  # Keine Standard-Buttons
        fade_container.addWidget(self.fade_duration_spin, stretch=1)
        
        # Plus-Button
        fade_plus_btn = QPushButton("+")
        fade_plus_btn.setStyleSheet("font-size: 32px; font-weight: bold; padding: 20px 30px; background: #2ecc71; color: white; border: none; border-radius: 12px; min-width: 80px; min-height: 60px;")
        fade_plus_btn.clicked.connect(lambda: self.fade_duration_spin.setValue(min(10.0, round(self.fade_duration_spin.value() + 0.1, 1))))
        fade_container.addWidget(fade_plus_btn)
        
        scroll_layout.addLayout(fade_container)
        
        # Sortierung (Dropdown)
        sort_label = QLabel("Sortierung:")
        sort_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(sort_label)
        
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("Nach Übertragungszeit", "transfer_time")
        self.sort_combo.addItem("Nach Erstellungszeit (EXIF)", "creation_time")
        self.sort_combo.addItem("Zufällig", "random")
        self.sort_combo.setStyleSheet("""
            QComboBox {
                font-size: 22px; color: #ecf0f1; padding: 15px;
                background: #2c3e50; border: 2px solid #34495e;
                border-radius: 12px; min-height: 50px;
            }
            QComboBox:hover {
                border-color: #3498db;
            }
            QComboBox::drop-down {
                border: none; width: 50px;
            }
            QComboBox::down-arrow {
                image: none; border-left: 5px solid transparent;
                border-right: 5px solid transparent; border-top: 8px solid #ecf0f1;
                width: 0; height: 0; margin-right: 15px;
            }
            QComboBox QAbstractItemView {
                background: #2c3e50; border: 2px solid #34495e;
                border-radius: 8px; color: #ecf0f1; font-size: 20px;
                selection-background-color: #3498db; padding: 10px;
            }
        """)
        scroll_layout.addWidget(self.sort_combo)
        
        # Deprecated: Shuffle-Checkbox (für Kompatibilität, wird durch sort_combo ersetzt)
        self.shuffle_check = QCheckBox("Zufällige Reihenfolge (veraltet)")
        self.shuffle_check.setStyleSheet("""
            QCheckBox {
                font-size: 18px; color: #95a5a6; padding: 10px 0;
                spacing: 15px;
            }
            QCheckBox::indicator {
                width: 30px; height: 30px;
                border: 2px solid #34495e; border-radius: 6px;
                background: #2c3e50;
            }
            QCheckBox::indicator:checked {
                background: #2ecc71; border-color: #27ae60;
            }
        """)
        self.shuffle_check.setVisible(False)  # Versteckt, aber vorhanden für Kompatibilität
        scroll_layout.addWidget(self.shuffle_check)
        
        # Email-Einstellungen (einfach, ohne Container)
        email_group = QLabel("Email")
        email_group.setStyleSheet("font-size: 24px; font-weight: bold; color: #ecf0f1; padding: 20px 0 10px 0; border-bottom: 2px solid #34495e;")
        scroll_layout.addWidget(email_group)
        
        # IMAP Server
        imap_label = QLabel("IMAP Server:")
        imap_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(imap_label)
        imap_container = QHBoxLayout()
        imap_container.setSpacing(10)
        self.imap_server_edit = QLineEdit()
        self.imap_server_edit.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        
        # Sichere mousePressEvent-Behandlung
        def imap_mouse_press(event):
            try:
                QLineEdit.mousePressEvent(self.imap_server_edit, event)
                # Sichere Lambda-Funktion mit Prüfung
                def safe_show_keyboard():
                    try:
                        if self.imap_server_edit and hasattr(self, 'show_system_keyboard'):
                            self.show_system_keyboard(self.imap_server_edit)
                    except Exception as e:
                        logger.error(f"Fehler beim Anzeigen der Tastatur (IMAP): {e}", exc_info=True)
                QTimer.singleShot(200, safe_show_keyboard)
            except Exception as e:
                logger.error(f"Fehler in imap_mouse_press: {e}", exc_info=True)
        self.imap_server_edit.mousePressEvent = imap_mouse_press
        imap_container.addWidget(self.imap_server_edit)
        imap_keyboard_btn = QPushButton("⌨ Tastatur")
        imap_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        imap_keyboard_btn.clicked.connect(lambda checked=False: self.show_system_keyboard(self.imap_server_edit))
        imap_container.addWidget(imap_keyboard_btn)
        scroll_layout.addLayout(imap_container)
        
        # Benutzername
        username_label = QLabel("Benutzername:")
        username_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(username_label)
        username_container = QHBoxLayout()
        username_container.setSpacing(10)
        self.username_edit = QLineEdit()
        self.username_edit.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        
        # Sichere mousePressEvent-Behandlung
        def username_mouse_press(event):
            try:
                QLineEdit.mousePressEvent(self.username_edit, event)
                # Sichere Lambda-Funktion mit Prüfung
                def safe_show_keyboard():
                    try:
                        if self.username_edit and hasattr(self, 'show_system_keyboard'):
                            self.show_system_keyboard(self.username_edit)
                    except Exception as e:
                        logger.error(f"Fehler beim Anzeigen der Tastatur (Username): {e}", exc_info=True)
                QTimer.singleShot(200, safe_show_keyboard)
            except Exception as e:
                logger.error(f"Fehler in username_mouse_press: {e}", exc_info=True)
        self.username_edit.mousePressEvent = username_mouse_press
        username_container.addWidget(self.username_edit)
        username_keyboard_btn = QPushButton("⌨ Tastatur")
        username_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        username_keyboard_btn.clicked.connect(lambda checked=False: self.show_system_keyboard(self.username_edit))
        username_container.addWidget(username_keyboard_btn)
        scroll_layout.addLayout(username_container)
        
        # Passwort
        password_label = QLabel("Passwort:")
        password_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(password_label)
        password_container = QHBoxLayout()
        password_container.setSpacing(10)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        
        # Sichere mousePressEvent-Behandlung
        def password_mouse_press(event):
            try:
                QLineEdit.mousePressEvent(self.password_edit, event)
                # Sichere Lambda-Funktion mit Prüfung
                def safe_show_keyboard():
                    try:
                        if self.password_edit and hasattr(self, 'show_system_keyboard'):
                            self.show_system_keyboard(self.password_edit)
                    except Exception as e:
                        logger.error(f"Fehler beim Anzeigen der Tastatur (Password): {e}", exc_info=True)
                QTimer.singleShot(200, safe_show_keyboard)
            except Exception as e:
                logger.error(f"Fehler in password_mouse_press: {e}", exc_info=True)
        self.password_edit.mousePressEvent = password_mouse_press
        password_container.addWidget(self.password_edit)
        password_keyboard_btn = QPushButton("⌨ Tastatur")
        password_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        password_keyboard_btn.clicked.connect(lambda: self.show_system_keyboard(self.password_edit))
        password_container.addWidget(password_keyboard_btn)
        scroll_layout.addLayout(password_container)
        
        # Auto-Reply
        self.auto_reply_check = QCheckBox("Automatische Antwort senden")
        self.auto_reply_check.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(self.auto_reply_check)
        
        # Email Account testen Button
        email_test_btn = QPushButton("Email Account testen")
        email_test_btn.setStyleSheet("font-size: 20px; font-weight: bold; padding: 15px; background: #3498db; color: white; border: none; border-radius: 10px; margin-top: 10px;")
        email_test_btn.clicked.connect(self.test_email_connection)
        scroll_layout.addWidget(email_test_btn)
        
        # Status-Label für Email-Test
        self.email_test_status = QLabel("")
        self.email_test_status.setStyleSheet("font-size: 18px; padding: 10px; margin-top: 10px;")
        self.email_test_status.setWordWrap(True)
        scroll_layout.addWidget(self.email_test_status)
        
        scroll_layout.addStretch()
        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Button-Leiste
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        # Speichern-Button
        save_btn = QPushButton("Einstellungen speichern")
        save_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 20px; background: #2ecc71; color: white; border: none; border-radius: 12px;")
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def show_system_keyboard(self, input_field):
        """Zeigt die eigene Touch-Tastatur"""
        try:
            logger.info("=== Touch-Tastatur wird angezeigt (Einstellungen) ===")
            
            # Prüfe ob input_field noch existiert und gültig ist
            try:
                if not input_field:
                    logger.warning("Ungültiges Eingabefeld für Tastatur: None")
                    return
                # Prüfe ob Widget noch existiert durch Zugriff auf eine Eigenschaft
                _ = input_field.isVisible()
                if not hasattr(input_field, 'setFocus'):
                    logger.warning("Eingabefeld hat keine setFocus-Methode")
                    return
            except RuntimeError:
                logger.warning("Eingabefeld wurde gelöscht")
                return
            except Exception as e:
                logger.warning(f"Fehler beim Prüfen des Eingabefelds: {e}")
                return
            
            self.current_input = input_field
            
            # Setze Focus sicher
            try:
                input_field.setFocus()
            except Exception as e:
                logger.warning(f"Fehler beim Setzen des Focus: {e}")
            
            # Prüfe ob self noch existiert
            try:
                _ = self.isVisible()
            except RuntimeError:
                logger.warning("SettingsWidget wurde gelöscht")
                return
            
            # Erstelle oder aktualisiere Tastatur-Widget
            if not self.keyboard_widget:
                try:
                    self.keyboard_widget = TouchKeyboard(self, input_field)
                except Exception as e:
                    logger.error(f"Fehler beim Erstellen der Tastatur: {e}", exc_info=True)
                    return
            else:
                # Prüfe ob keyboard_widget noch existiert
                try:
                    _ = self.keyboard_widget.isVisible()
                    self.keyboard_widget.set_input_field(input_field)
                except RuntimeError:
                    # Widget wurde gelöscht, erstelle neues
                    logger.warning("Tastatur-Widget wurde gelöscht, erstelle neues")
                    try:
                        self.keyboard_widget = TouchKeyboard(self, input_field)
                    except Exception as e:
                        logger.error(f"Fehler beim Neuerstellen der Tastatur: {e}", exc_info=True)
                        return
            
            # Aktualisiere Position der Tastatur
            try:
                parent_height = self.height() if hasattr(self, 'height') else 600
                parent_width = self.width() if hasattr(self, 'width') else 1024
            except Exception:
                parent_height = 600
                parent_width = 1024
            
            keyboard_height = 250
            
            # Prüfe ob keyboard_widget noch existiert vor setGeometry
            try:
                _ = self.keyboard_widget.isVisible()
                self.keyboard_widget.setGeometry(0, parent_height - keyboard_height - 40, parent_width, keyboard_height)
                self.keyboard_widget.show()
                self.keyboard_widget.raise_()
            except RuntimeError:
                logger.warning("Tastatur-Widget wurde während Positionierung gelöscht")
                self.keyboard_widget = None
            except Exception as e:
                logger.error(f"Fehler beim Positionieren der Tastatur: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Anzeigen der Tastatur: {e}", exc_info=True)
    
    def hide_system_keyboard(self):
        """Versteckt die Touch-Tastatur"""
        if self.keyboard_widget:
            self.keyboard_widget.hide()
    
    def load_settings(self):
        """Lädt die aktuellen Einstellungen aus der Config (Config wird vorher neu geladen)"""
        # Stelle sicher, dass Config aktuell ist
        try:
            self.config.config = self.config._load_config()
        except Exception as e:
            logger.warning(f"Konnte Config nicht neu laden: {e}")
        
        # Lade Werte aus Config
        self.auto_play_check.setChecked(self.config.get('slideshow.auto_play', True))
        self.interval_spin.setValue(self.config.get('slideshow.interval_seconds', 10))
        self.fade_duration_spin.setValue(self.config.get('slideshow.transition_duration', 1.0))
        
        # Sortierung laden (mit Fallback auf shuffle für alte Configs)
        sort_by = self.config.get('slideshow.sort_by', None)
        if sort_by is None:
            # Fallback: Verwende shuffle-Einstellung
            if self.config.get('slideshow.shuffle', False):
                sort_by = "random"
            else:
                sort_by = "transfer_time"
        
        # Setze ComboBox auf richtigen Wert
        index = self.sort_combo.findData(sort_by)
        if index >= 0:
            self.sort_combo.setCurrentIndex(index)
        else:
            self.sort_combo.setCurrentIndex(0)  # Fallback auf transfer_time
        
        self.shuffle_check.setChecked(self.config.get('slideshow.shuffle', False))
        self.imap_server_edit.setText(self.config.get('email.imap_server', ''))
        self.username_edit.setText(self.config.get('email.username', ''))
        self.password_edit.setText(self.config.get('email.password', ''))
        self.auto_reply_check.setChecked(self.config.get('email.auto_reply', True))
        logger.debug("Einstellungen aus Config geladen")
    
    def save_settings(self):
        """Speichert die Einstellungen und wendet sie sofort an"""
        old_interval = self.config.get('slideshow.interval_seconds', 10)
        old_shuffle = self.config.get('slideshow.shuffle', False)
        old_auto_play = self.config.get('slideshow.auto_play', True)
        old_sort_by = self.config.get('slideshow.sort_by', 'transfer_time')
        
        self.config.set('slideshow.auto_play', self.auto_play_check.isChecked())
        self.config.set('slideshow.interval_seconds', self.interval_spin.value())
        self.config.set('slideshow.transition_duration', self.fade_duration_spin.value())
        
        # Neue Sortierung speichern
        new_sort_by = self.sort_combo.currentData()
        if new_sort_by:
            self.config.set('slideshow.sort_by', new_sort_by)
            # Für Kompatibilität: shuffle entsprechend setzen
            self.config.set('slideshow.shuffle', (new_sort_by == "random"))
        else:
            # Fallback: Verwende shuffle-Checkbox
            shuffle_enabled = self.shuffle_check.isChecked()
            self.config.set('slideshow.shuffle', shuffle_enabled)
            self.config.set('slideshow.sort_by', "random" if shuffle_enabled else "transfer_time")
        self.config.set('email.imap_server', self.imap_server_edit.text())
        self.config.set('email.username', self.username_edit.text())
        self.config.set('email.password', self.password_edit.text())
        self.config.set('email.auto_reply', self.auto_reply_check.isChecked())
        
        # Einstellungen sofort anwenden
        if self.main_window:
            self.main_window.apply_settings()
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Erfolg")
        msg.setText("Einstellungen wurden gespeichert und sofort angewendet.")
        msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                         "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                         "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                         "QPushButton:hover { background-color: #2980b9; }")
        msg.exec_()
    
    def test_email_connection(self):
        """Testet die Email-Verbindung"""
        imap_server = self.imap_server_edit.text().strip()
        username = self.username_edit.text().strip()
        password = self.password_edit.text().strip()
        
        if not imap_server or not username or not password:
            self.email_test_status.setText("Bitte füllen Sie alle Felder aus.")
            self.email_test_status.setStyleSheet("font-size: 18px; padding: 10px; margin-top: 10px; color: #e74c3c;")
            return
        
        self.email_test_status.setText("Teste Email-Verbindung...")
        self.email_test_status.setStyleSheet("font-size: 18px; padding: 10px; margin-top: 10px; color: #3498db;")
        
        # Teste Email-Verbindung in einem Thread, damit die GUI nicht blockiert
        from threading import Thread
        
        def test_connection():
            try:
                from email_handler import EmailHandler
                email_handler = EmailHandler(server=imap_server, port=993, username=username, password=password)
                
                if email_handler.connect():
                    email_handler.disconnect()
                    # Verwende Signal für thread-sichere GUI-Updates
                    self.email_test_result.emit(True, "Email-Verbindung erfolgreich!")
                else:
                    # Verwende Signal für thread-sichere GUI-Updates
                    self.email_test_result.emit(False, "Verbindung fehlgeschlagen")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Fehler beim Email-Test: {error_msg}", exc_info=True)
                # Verwende Signal für thread-sichere GUI-Updates
                self.email_test_result.emit(False, error_msg)
        
        thread = Thread(target=test_connection, daemon=True)
        thread.start()
    
    def _on_email_test_result(self, success: bool, message: str):
        """Wird aufgerufen wenn Email-Test abgeschlossen ist (thread-sicher über Signal)"""
        try:
            if success:
                self.email_test_status.setText(f"✓ {message}")
                self.email_test_status.setStyleSheet("font-size: 18px; padding: 10px; margin-top: 10px; color: #27ae60;")
            else:
                self.email_test_status.setText(f"✗ Fehler: {message}")
                self.email_test_status.setStyleSheet("font-size: 18px; padding: 10px; margin-top: 10px; color: #e74c3c;")
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren des Email-Test-Status: {e}", exc_info=True)
    
    def go_back(self):
        """Signalisiert, zurück zur Slideshow zu gehen"""
        self.parent().setCurrentIndex(0)

class DisplaySettingsWidget(QWidget):
    """Bildschirm-Einstellungs-Widget"""
    def __init__(self, config: ConfigManager, main_window=None, parent_menu=None):
        super().__init__()
        self.config = config
        self.main_window = main_window
        self.parent_menu = parent_menu
        self.current_input = None
        self.keyboard_widget = None
        self.setup_ui()
        self.load_settings()
    
    def setup_ui(self):
        """Erstellt die UI-Elemente"""
        # Hintergrund ZUERST setzen
        self.setStyleSheet("background-color: #1a1a2e;")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#1a1a2e"))
        palette.setColor(self.foregroundRole(), QColor("#ffffff"))
        self.setPalette(palette)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)
        
        # Header mit Titel und Zurück-Button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Bildschirm-Einstellungen")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ffffff; padding: 5px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        back_btn = QPushButton("X")
        back_btn.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background: #e74c3c; border: none; border-radius: 18px; min-width: 45px; min-height: 45px;")
        back_btn.clicked.connect(self.go_back)
        header_layout.addWidget(back_btn)
        layout.addLayout(header_layout)
        
        # ScrollArea für scrollbare Einstellungen
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background-color: #1a1a2e; border: none; } "
                            "QScrollBar:vertical { background: #2c3e50; width: 15px; border-radius: 7px; } "
                            "QScrollBar::handle:vertical { background: #34495e; border-radius: 7px; min-height: 30px; } "
                            "QScrollBar::handle:vertical:hover { background: #3498db; }")
        scroll_content = QWidget()
        scroll_content.setStyleSheet("background-color: #1a1a2e;")
        scroll_content.setAutoFillBackground(True)
        palette = scroll_content.palette()
        palette.setColor(scroll_content.backgroundRole(), QColor("#1a1a2e"))
        scroll_content.setPalette(palette)
        scroll_layout = QVBoxLayout()
        scroll_layout.setContentsMargins(10, 10, 10, 10)
        scroll_layout.setSpacing(20)
        
        # Automatisches Ausschalten
        dpms_group = QLabel("Automatisches Ausschalten")
        dpms_group.setStyleSheet("font-size: 24px; font-weight: bold; color: #ecf0f1; padding: 10px 0; border-bottom: 2px solid #34495e;")
        scroll_layout.addWidget(dpms_group)
        
        # DPMS aktivieren/deaktivieren
        self.dpms_enabled_check = QCheckBox("Bildschirm automatisch ausschalten")
        self.dpms_enabled_check.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(self.dpms_enabled_check)
        
        # DPMS Standby-Zeit
        dpms_label = QLabel("Bildschirm ausschalten nach (Minuten, 0 = deaktiviert):")
        dpms_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(dpms_label)
        dpms_container = QHBoxLayout()
        dpms_container.setSpacing(10)
        self.dpms_standby_spin = QSpinBox()
        self.dpms_standby_spin.setRange(0, 1440)  # 0-24 Stunden
        self.dpms_standby_spin.setSuffix(" Min")
        self.dpms_standby_spin.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        dpms_container.addWidget(self.dpms_standby_spin)
        dpms_keyboard_btn = QPushButton("⌨ Tastatur")
        dpms_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        dpms_keyboard_btn.clicked.connect(lambda: self.show_system_keyboard(self.dpms_standby_spin))
        dpms_container.addWidget(dpms_keyboard_btn)
        scroll_layout.addLayout(dpms_container)
        
        # Zeitgesteuerte Ein/Ausschaltung
        schedule_group = QLabel("Zeitgesteuerte Ein/Ausschaltung")
        schedule_group.setStyleSheet("font-size: 24px; font-weight: bold; color: #ecf0f1; padding: 20px 0 10px 0; border-bottom: 2px solid #34495e;")
        scroll_layout.addWidget(schedule_group)
        
        # Zeitsteuerung aktivieren/deaktivieren
        self.schedule_enabled_check = QCheckBox("Zeitsteuerung aktivieren")
        self.schedule_enabled_check.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(self.schedule_enabled_check)
        
        # Einschaltzeit
        on_time_label = QLabel("Bildschirm einschalten um (HH:MM):")
        on_time_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(on_time_label)
        on_time_container = QHBoxLayout()
        on_time_container.setSpacing(10)
        self.on_time_edit = QLineEdit()
        self.on_time_edit.setPlaceholderText("08:00")
        self.on_time_edit.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        self.on_time_edit.mousePressEvent = lambda e: (QLineEdit.mousePressEvent(self.on_time_edit, e), QTimer.singleShot(200, lambda: self.show_system_keyboard(self.on_time_edit)))
        on_time_container.addWidget(self.on_time_edit)
        on_time_keyboard_btn = QPushButton("⌨ Tastatur")
        on_time_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        on_time_keyboard_btn.clicked.connect(lambda: self.show_system_keyboard(self.on_time_edit))
        on_time_container.addWidget(on_time_keyboard_btn)
        scroll_layout.addLayout(on_time_container)
        
        # Ausschaltzeit
        off_time_label = QLabel("Bildschirm ausschalten um (HH:MM):")
        off_time_label.setStyleSheet("font-size: 20px; color: #ecf0f1; padding: 10px 0;")
        scroll_layout.addWidget(off_time_label)
        off_time_container = QHBoxLayout()
        off_time_container.setSpacing(10)
        self.off_time_edit = QLineEdit()
        self.off_time_edit.setPlaceholderText("22:00")
        self.off_time_edit.setStyleSheet("font-size: 20px; padding: 15px; background: #2c3e50; color: #ecf0f1; border: 2px solid #34495e; border-radius: 10px;")
        self.off_time_edit.mousePressEvent = lambda e: (QLineEdit.mousePressEvent(self.off_time_edit, e), QTimer.singleShot(200, lambda: self.show_system_keyboard(self.off_time_edit)))
        off_time_container.addWidget(self.off_time_edit)
        off_time_keyboard_btn = QPushButton("⌨ Tastatur")
        off_time_keyboard_btn.setStyleSheet("font-size: 16px; font-weight: bold; padding: 10px 15px; background: #3498db; color: white; border: none; border-radius: 8px; min-width: 100px;")
        off_time_keyboard_btn.clicked.connect(lambda: self.show_system_keyboard(self.off_time_edit))
        off_time_container.addWidget(off_time_keyboard_btn)
        scroll_layout.addLayout(off_time_container)
        
        scroll_layout.addStretch()
        scroll_content.setLayout(scroll_layout)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        # Button-Leiste
        button_layout = QHBoxLayout()
        button_layout.setSpacing(15)
        
        # Speichern-Button
        save_btn = QPushButton("Einstellungen speichern")
        save_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 20px; background: #2ecc71; color: white; border: none; border-radius: 12px;")
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def show_system_keyboard(self, input_field):
        """Zeigt die eigene Touch-Tastatur"""
        logger.info("=== Touch-Tastatur wird angezeigt (Bildschirm-Einstellungen) ===")
        self.current_input = input_field
        input_field.setFocus()
        
        # Erstelle oder aktualisiere Tastatur-Widget
        if not self.keyboard_widget:
            self.keyboard_widget = TouchKeyboard(self, input_field)
        else:
            self.keyboard_widget.set_input_field(input_field)
        
        # Aktualisiere Position der Tastatur
        parent_height = self.height() if self else 600
        parent_width = self.width() if self else 1024
        keyboard_height = 250
        self.keyboard_widget.setGeometry(0, parent_height - keyboard_height - 40, parent_width, keyboard_height)
        
        self.keyboard_widget.show()
        self.keyboard_widget.raise_()
    
    def hide_system_keyboard(self):
        """Versteckt die Touch-Tastatur"""
        if self.keyboard_widget:
            self.keyboard_widget.hide()
    
    def load_settings(self):
        """Lädt die aktuellen Einstellungen"""
        self.dpms_enabled_check.setChecked(self.config.get('display.dpms_enabled', False))
        self.dpms_standby_spin.setValue(self.config.get('display.dpms_standby_minutes', 0))
        self.schedule_enabled_check.setChecked(self.config.get('display.schedule_enabled', False))
        self.on_time_edit.setText(self.config.get('display.schedule_on_time', '08:00'))
        self.off_time_edit.setText(self.config.get('display.schedule_off_time', '22:00'))
    
    def save_settings(self):
        """Speichert die Einstellungen und wendet sie sofort an"""
        # Validiere Uhrzeiten
        on_time = self.on_time_edit.text().strip()
        off_time = self.off_time_edit.text().strip()
        
        # Prüfe Format HH:MM
        try:
            if on_time:
                datetime.strptime(on_time, '%H:%M')
            if off_time:
                datetime.strptime(off_time, '%H:%M')
        except ValueError:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Fehler")
            msg.setText("Ungültiges Zeitformat! Bitte verwenden Sie HH:MM (z.B. 08:00)")
            msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                             "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                             "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                             "QPushButton:hover { background-color: #2980b9; }")
            msg.exec_()
            return
        
        self.config.set('display.dpms_enabled', self.dpms_enabled_check.isChecked())
        self.config.set('display.dpms_standby_minutes', self.dpms_standby_spin.value())
        self.config.set('display.schedule_enabled', self.schedule_enabled_check.isChecked())
        self.config.set('display.schedule_on_time', on_time if on_time else '08:00')
        self.config.set('display.schedule_off_time', off_time if off_time else '22:00')
        
        # Einstellungen sofort anwenden
        if self.main_window:
            self.main_window.apply_settings()
            self.main_window.apply_dpms_settings()
            self.main_window.setup_display_schedule()
        
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Erfolg")
        msg.setText("Einstellungen wurden gespeichert und sofort angewendet.")
        msg.setStyleSheet("QMessageBox { background-color: #2c3e50; color: #ecf0f1; } "
                         "QMessageBox QLabel { color: #ecf0f1; font-size: 16px; } "
                         "QPushButton { background-color: #3498db; color: white; padding: 10px 20px; border-radius: 8px; font-size: 16px; } "
                         "QPushButton:hover { background-color: #2980b9; }")
        msg.exec_()
    
    def go_back(self):
        """Geht zurück zum Menü"""
        try:
            # Verstecke Tastatur
            self.hide_system_keyboard()
            # Verstecke dieses Widget
            self.hide()
            # Zeige Slideshow wieder an
            if hasattr(self.main_window, 'slideshow_widget'):
                self.main_window.slideshow_widget.show()
            # Zeige Menü wieder an falls vorhanden (prüfe ob Widget noch existiert)
            if hasattr(self.main_window, 'current_menu') and self.main_window.current_menu:
                try:
                    # Prüfe ob Widget noch existiert (durch Zugriff auf eine Eigenschaft)
                    _ = self.main_window.current_menu.isVisible()
                    self.main_window.current_menu.show()
                    self.main_window.menu_visible = True
                except RuntimeError:
                    # Widget wurde bereits gelöscht, erstelle neues Menü
                    logger.debug("Menü-Widget wurde gelöscht, erstelle neues Menü")
                    self.main_window.current_menu = None
                    self.main_window.menu_visible = False
            logger.info("Zurück zum Menü (von Bildschirm-Einstellungen)")
        except Exception as e:
            logger.error(f"Fehler beim Zurückkehren zum Menü: {e}", exc_info=True)

class MainWindow(QMainWindow):
    """Hauptfenster der Anwendung"""
    def __init__(self):
        super().__init__()
        self.config = ConfigManager()
        self.image_processor = ImageProcessor(
            target_width=self.config.get('display.width', 1024),
            target_height=self.config.get('display.height', 600)
        )
        # Bestimme Sortierung (Fallback auf shuffle für Kompatibilität)
        sort_by = self.config.get('slideshow.sort_by', None)
        if sort_by is None:
            # Fallback: Verwende shuffle-Einstellung (für alte Configs)
            if self.config.get('slideshow.shuffle', False):
                sort_by = "random"
            else:
                sort_by = "transfer_time"
        
        metadata_file = Path(self.config.get('paths.proxy_images')) / 'metadata.json'
        self.slideshow = Slideshow(
            proxy_dir=Path(self.config.get('paths.proxy_images')),
            interval_seconds=self.config.get('slideshow.interval_seconds', 10),
            shuffle=self.config.get('slideshow.shuffle', False),  # Deprecated
            loop=self.config.get('slideshow.loop', True),
            sort_by=sort_by,
            original_dir=Path(self.config.get('paths.original_images')),
            metadata_file=metadata_file
        )
        
        self.setup_ui()
        
        self.settings_queue = None  # Wird von außen gesetzt
        
        # QR-Code-Umschaltung: True = Web-Interface, False = iOS Shortcut
        self.qr_code_mode_web = True
        
        # Initialisiere Slideshow SOFORT (nicht verzögert), damit das erste Bild schnell geladen wird
        # Verwende QTimer.singleShot(0) um es im nächsten Event-Loop-Zyklus auszuführen
        # Das stellt sicher, dass das Fenster bereits angezeigt wurde
        QTimer.singleShot(0, self._initialize_slideshow)
        
        # Langwierige Initialisierungen verzögert starten (nachdem Fenster angezeigt wurde)
        QTimer.singleShot(200, self._delayed_initialization)
    
    def _initialize_slideshow(self):
        """Initialisiert die Slideshow und lädt das erste Bild"""
        try:
            # Stelle sicher, dass das Fenster angezeigt wurde
            QApplication.processEvents()
            
            # Prüfe Bildanzahl - wenn 0, aktualisiere die Liste nochmal (falls Bilder hinzugefügt wurden)
            image_count = self.slideshow_widget.slideshow.get_image_count()
            if image_count == 0:
                logger.info("Keine Bilder beim Start, aktualisiere Slideshow-Liste...")
                self.slideshow_widget.slideshow._refresh_image_list()
                image_count = self.slideshow_widget.slideshow.get_image_count()
            
            # Lade erstes Bild ASYNCHRON (nicht blockierend)
            if image_count > 0:
                logger.info(f"Lade erstes Bild von {image_count} verfügbaren Bildern")
                # Verwende QTimer, um das Bild-Laden im Hintergrund zu starten
                # Das verhindert Blockierung der GUI
                QTimer.singleShot(50, lambda: self.slideshow_widget.load_current_image(use_fade=False))
            else:
                logger.info("Keine Bilder vorhanden beim Start")
            
            logger.info("Slideshow initialisiert")
        except Exception as e:
            logger.error(f"Fehler beim Initialisieren der Slideshow: {e}", exc_info=True)
    
    def _delayed_initialization(self):
        """Startet langwierige Initialisierungen nach dem Anzeigen des Fensters"""
        logger.info("Starte verzögerte Initialisierungen...")
        self.setup_email_checker()
        self.setup_file_watcher()
        self.apply_dpms_settings()  # DPMS-Einstellungen beim Start anwenden
        self.setup_display_schedule()  # Zeitgesteuerte Ein/Ausschaltung einrichten
        
        # Prüfe ob Bilder vorhanden sind und Slideshow aktualisieren falls nötig
        # Das ist wichtig, falls Bilder hinzugefügt wurden während die Anwendung bereits lief
        if hasattr(self, 'slideshow_widget') and self.slideshow_widget:
            image_count = self.slideshow_widget.slideshow.get_image_count()
            if image_count == 0:
                # Keine Bilder beim Start - prüfe ob jetzt welche vorhanden sind
                logger.info("Keine Bilder beim Start erkannt, prüfe erneut...")
                self.slideshow_widget.slideshow._refresh_image_list()
                new_count = self.slideshow_widget.slideshow.get_image_count()
                if new_count > 0:
                    logger.info(f"Bilder gefunden nach verzögerter Initialisierung: {new_count} Bilder")
                    # Lade erstes Bild
                    QTimer.singleShot(100, lambda: self.slideshow_widget.load_current_image(use_fade=False))
        
        logger.info("Verzögerte Initialisierungen abgeschlossen")
    
    def setup_ui(self):
        """Erstellt die UI"""
        # Hintergrund des MainWindow setzen
        self.setStyleSheet("background-color: #1a1a2e;")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor("#1a1a2e"))
        palette.setColor(self.foregroundRole(), QColor("#ffffff"))
        self.setPalette(palette)
        
        # Vollbild-Modus
        if self.config.get('display.fullscreen', True):
            self.showFullScreen()
        else:
            self.setGeometry(0, 0, 
                           self.config.get('display.width', 1024),
                           self.config.get('display.height', 600))
        
        # Stacked Widget für verschiedene Ansichten
        self.stacked = QStackedWidget()
        self.stacked.setStyleSheet("background-color: #1a1a2e;")
        self.stacked.setAutoFillBackground(True)
        stacked_palette = self.stacked.palette()
        stacked_palette.setColor(self.stacked.backgroundRole(), QColor("#1a1a2e"))
        self.stacked.setPalette(stacked_palette)
        self.setCentralWidget(self.stacked)
        
        # Slideshow-Widget (mit Referenz zu MainWindow für Menü)
        self.slideshow_widget = SlideshowWidget(self.slideshow, self.config, main_window=self)
        self.stacked.addWidget(self.slideshow_widget)
        self.stacked.setCurrentWidget(self.slideshow_widget)  # Zeige Slideshow-Widget direkt
        
        # Bildverwaltung
        self.image_mgmt_widget = ImageManagementWidget(self.config, self.image_processor)
        self.stacked.addWidget(self.image_mgmt_widget)
        
        # Einstellungen (mit Referenz zu MainWindow für Live-Updates)
        self.settings_widget = SettingsWidget(self.config, self)
        self.stacked.addWidget(self.settings_widget)
        
        # Menü-Buttons (oben rechts, nur sichtbar bei Berührung)
        self.setup_menu_buttons()
    
    def setup_menu_buttons(self):
        """Richtet Menü-Buttons ein (erscheinen bei längerem Touch)"""
        self.menu_timer = QTimer()
        self.menu_timer.setSingleShot(True)
        self.menu_timer.timeout.connect(self.show_menu)
        self.menu_visible = False
    
    def show_menu(self):
        """Zeigt das Menü an"""
        if self.menu_visible:
            return
        
        self.menu_visible = True
        
        # Overlay-Widget für Menü (nicht transparent)
        menu_widget = QWidget(self)
        menu_widget.setStyleSheet("background-color: #1a1a2e;")  # Dunkles Blau-Grau
        menu_layout = QVBoxLayout()
        menu_layout.setContentsMargins(15, 10, 15, 10)  # Reduziertes Padding
        menu_layout.setSpacing(10)  # Reduzierter Abstand
        
        # Header mit Titel und X-Button
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("MENU")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ffffff; padding: 5px;")
        header_layout.addWidget(title)
        header_layout.addStretch()
        close_btn = QPushButton("X")
        close_btn.setStyleSheet("font-size: 18px; font-weight: bold; color: white; background: #e74c3c; border: none; border-radius: 18px; min-width: 45px; min-height: 45px;")
        close_btn.clicked.connect(lambda: self.close_menu(menu_widget))
        header_layout.addWidget(close_btn)
        menu_layout.addLayout(header_layout)
        
        # WLAN-Verbindungsinfo (kompakter)
        wifi_info_widget = self.create_wifi_info_widget()
        wifi_info_widget.setObjectName('wifi_info_widget')
        menu_layout.addWidget(wifi_info_widget)
        
        # Button-Layout (kompakter)
        button_layout = QVBoxLayout()
        button_layout.setSpacing(10)  # Reduzierter Abstand zwischen Buttons
        
        # WLAN-Einstellungen Button
        wifi_btn = QPushButton("WLAN-Einstellungen")
        wifi_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; background: #3498db; color: white; border: none; border-radius: 12px;")
        wifi_btn.clicked.connect(lambda: self.show_wifi_settings(menu_widget))
        button_layout.addWidget(wifi_btn)
        
        # Bildverwaltung Button
        images_btn = QPushButton("Bilder verwalten")
        images_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; background: #2ecc71; color: white; border: none; border-radius: 12px;")
        images_btn.clicked.connect(lambda: self.show_image_management(menu_widget))
        button_layout.addWidget(images_btn)
        
        # Bildschirm-Einstellungen Button
        display_btn = QPushButton("Bildschirm-Einstellungen")
        display_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; background: #9b59b6; color: white; border: none; border-radius: 12px;")
        display_btn.clicked.connect(lambda: self.show_display_settings(menu_widget))
        button_layout.addWidget(display_btn)
        
        # Einstellungen Button
        settings_btn = QPushButton("Einstellungen")
        settings_btn.setStyleSheet("font-size: 22px; font-weight: bold; padding: 18px; background: #95a5a6; color: white; border: none; border-radius: 12px;")
        settings_btn.clicked.connect(lambda: self.show_settings(menu_widget))
        button_layout.addWidget(settings_btn)
        
        menu_layout.addLayout(button_layout)
        menu_layout.addStretch()
        
        menu_widget.setLayout(menu_layout)
        menu_widget.setGeometry(0, 0, self.width(), self.height())
        menu_widget.show()
        
        self.current_menu = menu_widget
        self.menu_wifi_info_widget = wifi_info_widget  # Speichere Referenz für Updates
    
    def update_wifi_info_in_menu(self):
        """Aktualisiert das WLAN-Info-Widget im aktuellen Menü"""
        if hasattr(self, 'current_menu') and self.current_menu:
            try:
                # Prüfe ob Widget noch existiert
                _ = self.current_menu.isVisible()
            except RuntimeError:
                # Widget wurde bereits gelöscht
                logger.debug("Menü-Widget wurde gelöscht, kann WLAN-Info nicht aktualisieren")
                return
            
            # Finde das WLAN-Info-Widget im Menü-Layout
            menu_layout = self.current_menu.layout()
            if menu_layout:
                # Durchsuche alle Widgets im Layout
                for i in range(menu_layout.count()):
                    item = menu_layout.itemAt(i)
                    if item and item.widget():
                        widget = item.widget()
                        # Prüfe ob es das WLAN-Info-Widget ist (hat ObjectName oder ist vom Typ QWidget mit bestimmten Eigenschaften)
                        if widget.objectName() == 'wifi_info_widget' or (isinstance(widget, QWidget) and widget.styleSheet() and 'WLAN-Verbindung' in str(widget.findChildren(QLabel))):
                            # Erstelle neues Widget mit aktuellen Daten
                            new_wifi_info = self.create_wifi_info_widget()
                            new_wifi_info.setObjectName('wifi_info_widget')
                            
                            # Ersetze das alte Widget
                            menu_layout.removeWidget(widget)
                            widget.deleteLater()
                            menu_layout.insertWidget(i, new_wifi_info)
                            self.menu_wifi_info_widget = new_wifi_info
                            logger.info("WLAN-Info im Menü aktualisiert")
                            return
                # Falls nicht gefunden, füge es am Anfang hinzu (nach Header)
                logger.warning("WLAN-Info-Widget nicht gefunden, füge neu hinzu")
                new_wifi_info = self.create_wifi_info_widget()
                new_wifi_info.setObjectName('wifi_info_widget')
                menu_layout.insertWidget(1, new_wifi_info)  # Nach Header (Index 0)
                self.menu_wifi_info_widget = new_wifi_info
    
    def create_wifi_info_widget(self):
        """Erstellt Widget mit WLAN-Verbindungsinfo und QR-Code"""
        widget = QWidget()
        widget.setStyleSheet("background: #2c3e50; border-radius: 12px; padding: 10px; margin: 5px 0; border: 2px solid #34495e;")
        layout = QHBoxLayout()  # Horizontal für halbierte Anzeige
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)
        
        # Linke Hälfte: Text-Info (kompakter)
        left_layout = QVBoxLayout()
        left_layout.setSpacing(3)
        title = QLabel("WLAN-Verbindung")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ecf0f1; padding: 2px 0;")
        left_layout.addWidget(title)
        
        # WLAN-SSID
        try:
            # Versuche SSID über iwgetid oder nmcli zu bekommen
            result = subprocess.run(['iwgetid', '-r'], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                ssid = result.stdout.strip()
            else:
                # Fallback: nmcli (Format: "yes:SSID" oder "nein:")
                result = subprocess.run(['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'], 
                                      capture_output=True, text=True, timeout=2)
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    ssid = "Nicht verbunden"
                    for line in lines:
                        # Prüfe auf "yes:" (aktiv) oder "ja:" (deutsch)
                        if line.startswith('yes:') or line.startswith('ja:'):
                            parts = line.split(':', 1)
                            if len(parts) > 1 and parts[1]:
                                ssid = parts[1]
                                break
        except Exception as e:
            # SSID konnte nicht abgerufen werden
            ssid = "Nicht verbunden"
        
        ssid_label = QLabel(f"WLAN: {ssid}")
        ssid_label.setStyleSheet("font-size: 14px; color: #bdc3c7; padding: 1px 0;")
        left_layout.addWidget(ssid_label)
        
        # Hostname
        try:
            hostname = socket.gethostname()
        except:
            hostname = "Unbekannt"
        
        hostname_label = QLabel(f"Hostname: {hostname}")
        hostname_label.setStyleSheet("font-size: 14px; color: #bdc3c7; padding: 1px 0;")
        left_layout.addWidget(hostname_label)
        
        # IP-Adresse
        try:
            # Hole alle IP-Adressen
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
            ip_addresses = result.stdout.strip().split()
            if ip_addresses:
                ip_text = ip_addresses[0]  # Erste IP verwenden
            else:
                ip_text = "Nicht verbunden"
        except:
            try:
                # Fallback: Socket-Methode
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip_text = s.getsockname()[0]
                s.close()
            except:
                ip_text = "Nicht verbunden"
        
        ip_label = QLabel(f"IP: {ip_text}")
        ip_label.setStyleSheet("font-size: 14px; color: #bdc3c7; padding: 1px 0;")
        left_layout.addWidget(ip_label)
        
        left_layout.addStretch()
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        layout.addWidget(left_widget)
        
        # Rechte Hälfte: QR-Code
        if QRCODE_AVAILABLE and ip_text != "Nicht verbunden":
            try:
                # Erstelle beide QR-Codes
                port = self.config.get('web.port', 80)
                web_url = f"http://{ip_text}:{port}"
                shortcut_url = "https://www.icloud.com/shortcuts/a309109b0b774647aad21dbbbe0a864c"
                
                # Funktion zum Erstellen eines QR-Codes
                def create_qr_code(data):
                    qr = qrcode.QRCode(version=1, box_size=4, border=2)
                    qr.add_data(data)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="#ecf0f1", back_color="#2c3e50")
                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    qimage = QImage()
                    qimage.loadFromData(img_bytes.read())
                    qpixmap = QPixmap.fromImage(qimage)
                    max_size = 120
                    if qpixmap.width() > max_size or qpixmap.height() > max_size:
                        qpixmap = qpixmap.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    return qpixmap
                
                # Erstelle beide QR-Codes
                web_qr_pixmap = create_qr_code(web_url)
                shortcut_qr_pixmap = create_qr_code(shortcut_url)
                
                # Funktion zum Umschalten des QR-Codes
                def toggle_qr_code():
                    self.qr_code_mode_web = not self.qr_code_mode_web
                    if self.qr_code_mode_web:
                        qr_label.setPixmap(web_qr_pixmap)
                        url_label.setText(f"Web:\n{web_url}")
                    else:
                        qr_label.setPixmap(shortcut_qr_pixmap)
                        url_label.setText("iOS Shortcut:\nBilder hochladen")
                
                # Initialisiere mit Web-Interface QR-Code
                qr_label = QLabel()
                qr_label.setPixmap(web_qr_pixmap)
                qr_label.setAlignment(Qt.AlignCenter)
                qr_label.setStyleSheet("background: #2c3e50; padding: 5px; border-radius: 6px; border: 2px solid #34495e; cursor: pointer;")
                qr_label.mousePressEvent = lambda e: toggle_qr_code()  # Klick-Handler
                
                right_layout = QVBoxLayout()
                right_layout.setSpacing(3)
                right_layout.addWidget(qr_label)
                url_label = QLabel(f"Web:\n{web_url}")
                url_label.setStyleSheet("font-size: 11px; color: #ecf0f1; padding: 2px;")
                url_label.setAlignment(Qt.AlignCenter)
                url_label.setWordWrap(True)
                right_layout.addWidget(url_label)
                
                right_widget = QWidget()
                right_widget.setLayout(right_layout)
                layout.addWidget(right_widget)
            except Exception as e:
                logger.error(f"Fehler beim Erstellen des QR-Codes: {e}")
                # Fallback: Text-Info
                qr_label = QLabel("QR-Code\nnicht verfügbar")
                qr_label.setStyleSheet("font-size: 16px; color: #bdc3c7; padding: 15px; background: #34495e; border-radius: 8px;")
                qr_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(qr_label)
        else:
            # Fallback wenn QR-Code nicht verfügbar
            qr_label = QLabel("QR-Code\nnicht verfügbar")
            qr_label.setStyleSheet("font-size: 16px; color: #bdc3c7; padding: 15px; background: #34495e; border-radius: 8px;")
            qr_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(qr_label)
        
        widget.setLayout(layout)
        return widget
    
    def show_wifi_settings(self, parent_menu):
        """Zeigt WLAN-Einstellungen mit Touch-Tastatur"""
        try:
            # Verstecke Menü
            parent_menu.hide()
            self.menu_visible = False
            
            # Verstecke Slideshow
            self.slideshow_widget.hide()
            
            # Erstelle WLAN-Widget
            wifi_widget = WifiSettingsWidget(self.config, self)
            wifi_widget.setParent(self)  # Parent ist MainWindow, nicht parent_menu
            wifi_widget.setGeometry(0, 0, self.width(), self.height())
            wifi_widget.setAutoFillBackground(True)  # Stelle sicher, dass Hintergrund nicht transparent ist
            # Stelle sicher, dass Palette gesetzt ist
            palette = wifi_widget.palette()
            palette.setColor(wifi_widget.backgroundRole(), QColor("#1a1a2e"))
            wifi_widget.setPalette(palette)
            wifi_widget.raise_()  # Nach vorne bringen
            wifi_widget.show()
            self.current_wifi_widget = wifi_widget
            logger.info("WLAN-Einstellungen im Menü geöffnet")
        except Exception as e:
            logger.error(f"Fehler beim Öffnen der WLAN-Einstellungen: {e}", exc_info=True)
            # Falls Fehler, Menü wieder anzeigen
            parent_menu.show()
            self.menu_visible = True
    
    def show_image_management(self, parent_menu):
        """Zeigt Bildverwaltung im Menü"""
        try:
            # Verstecke Menü
            parent_menu.hide()
            self.menu_visible = False
            
            # Verstecke Slideshow
            self.slideshow_widget.hide()
            
            # Erstelle Bildverwaltungs-Widget
            image_widget = MenuImageManagementWidget(self.config, self.image_processor, self, parent_menu)
            image_widget.setParent(self)  # Parent ist MainWindow, nicht parent_menu
            image_widget.setGeometry(0, 0, self.width(), self.height())
            # Stelle sicher, dass Hintergrund wirklich dunkel ist
            image_widget.setStyleSheet("background-color: #1a1a2e;")
            image_widget.setAutoFillBackground(True)
            palette = image_widget.palette()
            palette.setColor(image_widget.backgroundRole(), QColor("#1a1a2e"))
            palette.setColor(image_widget.foregroundRole(), QColor("#ffffff"))
            image_widget.setPalette(palette)
            image_widget.raise_()  # Nach vorne bringen
            image_widget.show()
            self.current_image_widget = image_widget
            logger.info("Bildverwaltung im Menü geöffnet")
        except Exception as e:
            logger.error(f"Fehler beim Öffnen der Bildverwaltung: {e}", exc_info=True)
            # Falls Fehler, Menü wieder anzeigen
            parent_menu.show()
            self.menu_visible = True
    
    def show_display_settings(self, parent_menu):
        """Zeigt Bildschirm-Einstellungen"""
        try:
            # Verstecke Menü (nur verstecken, nicht löschen - wie bei WLAN-Einstellungen)
            parent_menu.hide()
            self.menu_visible = False
            # Verstecke Slideshow
            self.slideshow_widget.hide()
            
            # Erstelle Bildschirm-Einstellungs-Widget
            display_widget = DisplaySettingsWidget(self.config, self, parent_menu)
            display_widget.setParent(self)  # Parent ist MainWindow
            display_widget.setGeometry(0, 0, self.width(), self.height())
            display_widget.setStyleSheet("background-color: #1a1a2e;")
            display_widget.setAutoFillBackground(True)
            palette = display_widget.palette()
            palette.setColor(display_widget.backgroundRole(), QColor("#1a1a2e"))
            palette.setColor(display_widget.foregroundRole(), QColor("#ffffff"))
            display_widget.setPalette(palette)
            display_widget.raise_()
            display_widget.show()
            self.current_display_widget = display_widget
            logger.info("Bildschirm-Einstellungen geöffnet")
        except Exception as e:
            logger.error(f"Fehler beim Öffnen der Bildschirm-Einstellungen: {e}", exc_info=True)
            # Falls Fehler, Menü wieder anzeigen
            parent_menu.show()
            self.menu_visible = True
    
    def show_settings(self, parent_menu):
        """Zeigt Einstellungen"""
        try:
            # Verstecke Menü (nur verstecken, nicht löschen - wie bei WLAN-Einstellungen)
            parent_menu.hide()
            self.menu_visible = False
            # Lade aktuelle Einstellungen in die GUI-Felder (wichtig: Config könnte vom Webinterface aktualisiert worden sein)
            if hasattr(self, 'settings_widget') and self.settings_widget:
                # Config neu laden bevor Felder aktualisiert werden
                self.config.config = self.config._load_config()
                self.settings_widget.load_settings()
            # Zeige Einstellungen im StackedWidget
            self.stacked.setCurrentIndex(2)
            logger.info("Einstellungen geöffnet")
        except Exception as e:
            logger.error(f"Fehler beim Öffnen der Einstellungen: {e}", exc_info=True)
    
    def close_menu(self, menu_widget):
        """Schließt das Menü (versteckt es, löscht es aber nicht)"""
        menu_widget.hide()
        self.menu_visible = False
        # Gehe zurück zur Slideshow
        if hasattr(self, 'stacked'):
            self.stacked.setCurrentIndex(0)
    
    def hide_menu(self, menu_widget):
        """Versteckt und löscht das Menü (nur für vollständiges Schließen)"""
        menu_widget.hide()
        menu_widget.deleteLater()
        self.menu_visible = False
    
    def mousePressEvent(self, event):
        """Erkennt langes Drücken für Menü"""
        if event.button() == Qt.LeftButton:
            self.menu_timer.start(3000)  # 3 Sekunden
    
    def mouseReleaseEvent(self, event):
        """Stoppt den Menü-Timer"""
        self.menu_timer.stop()
    
    def setup_email_checker(self):
        """Richtet den Email-Checker ein"""
        # Stoppe alten Timer, falls vorhanden
        if hasattr(self, 'email_timer') and self.email_timer:
            self.email_timer.stop()
            self.email_timer.deleteLater()
        
        # Email-Checker: 1x pro Minute (60000 ms)
        check_interval = 60 * 1000  # 1 Minute in Millisekunden
        self.email_timer = QTimer()
        self.email_timer.timeout.connect(self.check_emails)
        self.email_timer.start(check_interval)
        logger.info(f"Email-Checker gestartet mit Intervall: 1 Minute")
    
    def setup_file_watcher(self):
        """Richtet den File-Watcher für automatische Bilderkennung ein"""
        try:
            proxy_dir = Path(self.config.get('paths.proxy_images'))
            # Übergebe das refresh_requested Signal für thread-sichere Kommunikation
            refresh_signal = self.slideshow_widget.refresh_requested if self.slideshow_widget else None
            self.file_watcher = FileWatcher(
                proxy_dir=proxy_dir,
                slideshow=self.slideshow,
                slideshow_widget=self.slideshow_widget,
                refresh_signal=refresh_signal
            )
            self.file_watcher.start()
            logger.info("File-Watcher für automatische Bilderkennung gestartet")
        except Exception as e:
            logger.error(f"Fehler beim Starten des File-Watchers: {e}")
            self.file_watcher = None
    
    def apply_settings(self):
        """Wendet geänderte Einstellungen sofort an"""
        # Verhindere rekursive Aufrufe
        if hasattr(self, '_applying_settings') and self._applying_settings:
            logger.warning("apply_settings() bereits in Ausführung, überspringe rekursiven Aufruf")
            return
        
        self._applying_settings = True
        
        try:
            logger.info("Wende geänderte Einstellungen an...")
            
            # Altes Intervall ZUERST ermitteln (bevor wir es ändern)
            # Versuche es aus dem Widget-Timer zu lesen (genaueste Quelle)
            old_interval = None
            if hasattr(self, 'slideshow_widget') and hasattr(self.slideshow_widget, 'timer'):
                if self.slideshow_widget.timer.isActive():
                    old_interval = self.slideshow_widget.timer.interval() // 1000
            
            # Fallback: Aus Slideshow-Objekt
            if old_interval is None:
                old_interval = getattr(self.slideshow, 'interval_seconds', None)
            
            # Fallback: Standard-Wert
            if old_interval is None:
                old_interval = 10
            
            # Config NEU LADEN (wichtig: Config könnte vom Webinterface aktualisiert worden sein)
            self.config.config = self.config._load_config()
            
            # Alte auto_play Einstellung ermitteln
            old_auto_play = self.config.get('slideshow.auto_play', True)
            
            # Slideshow-Einstellungen aktualisieren
            new_interval = self.config.get('slideshow.interval_seconds', 10)
            new_shuffle = self.config.get('slideshow.shuffle', False)
            new_auto_play = self.config.get('slideshow.auto_play', True)
            
            # Neue Sortierung ermitteln (mit Fallback auf shuffle)
            new_sort_by = self.config.get('slideshow.sort_by', None)
            if new_sort_by is None:
                # Fallback: Verwende shuffle-Einstellung
                if new_shuffle:
                    new_sort_by = "random"
                else:
                    new_sort_by = "transfer_time"
            
            # Slideshow-Intervall in Slideshow-Objekt aktualisieren
            self.slideshow.interval_seconds = new_interval
            
            # Slideshow-Sortierung aktualisieren
            old_sort_by = getattr(self.slideshow, 'sort_by', 'transfer_time')
            if old_sort_by != new_sort_by or not hasattr(self.slideshow, 'sort_by'):
                self.slideshow.sort_by = new_sort_by
                # Aktualisiere original_dir falls nötig
                if not hasattr(self.slideshow, 'original_dir') or self.slideshow.original_dir is None:
                    self.slideshow.original_dir = Path(self.config.get('paths.original_images'))
                # Bilderliste neu sortieren
                self.slideshow._refresh_image_list()
                logger.info(f"Slideshow-Sortierung geändert: {old_sort_by} → {new_sort_by}")
            
            # Für Kompatibilität: shuffle-Einstellung aktualisieren
            self.slideshow.shuffle = (new_sort_by == "random")
            
            # Automatische Slideshow-Einstellung prüfen und anwenden
            new_auto_play = self.config.get('slideshow.auto_play', True)
            if old_auto_play != new_auto_play:
                logger.info(f"Automatische Slideshow: {'aktiviert' if new_auto_play else 'deaktiviert'}")
            
            # Timer entsprechend starten/stoppen (IMMER prüfen und korrekt setzen)
            if self.slideshow_widget and hasattr(self.slideshow_widget, 'timer'):
                # Timer IMMER erst stoppen (um sicherzustellen, dass er nicht läuft wenn auto_play false ist)
                if self.slideshow_widget.timer.isActive():
                    self.slideshow_widget.timer.stop()
                
                # Timer NUR starten wenn auto_play aktiviert ist
                if new_auto_play:
                    interval = new_interval * 1000
                    self.slideshow_widget.timer.setInterval(interval)
                    self.slideshow_widget.timer.start(interval)
                    logger.info(f"Timer gestartet mit Intervall: {new_interval}s (auto_play aktiviert)")
                else:
                    # Timer bleibt gestoppt (wurde bereits oben gestoppt)
                    logger.info("Timer gestoppt (auto_play deaktiviert)")
            
            # Aktualisiere auch die GUI-Felder falls Einstellungsseite offen ist
            # WICHTIG: load_settings() NICHT aufrufen, da dies apply_settings() wieder triggern könnte
            # Die Felder werden beim Öffnen der Einstellungsseite geladen (show_settings())
            
            # Slideshow-Widget aktualisieren (aktualisiert auch Timer-Intervall)
            # WICHTIG: refresh() kann resizeEvent() triggern, was wiederum Timer-Logik ausführt
            # Daher nur refresh() aufrufen wenn nötig
            if self.slideshow_widget:
                try:
                    # Nur Bildliste aktualisieren, nicht den gesamten refresh() der resizeEvent() triggert
                    self.slideshow_widget.slideshow.refresh()
                    # Aktualisiere nur das aktuelle Bild, ohne resizeEvent() zu triggern
                    if self.slideshow_widget.slideshow.get_image_count() > 0:
                        self.slideshow_widget.load_current_image(use_fade=False)
                except Exception as e:
                    logger.warning(f"Fehler beim Aktualisieren der Slideshow: {e}")
                if old_interval != new_interval:
                    logger.info(f"Slideshow-Intervall geändert: {old_interval}s → {new_interval}s")
                else:
                    logger.info(f"Slideshow-Intervall: {new_interval}s (unverändert)")
            
            # Email-Checker neu konfigurieren
            self.setup_email_checker()
            logger.info("Email-Checker neu konfiguriert")
            
            # DPMS-Einstellungen anwenden
            self.apply_dpms_settings()
            
            # Zeitgesteuerte Ein/Ausschaltung neu einrichten
            self.setup_display_schedule()
            
            logger.info("Einstellungen erfolgreich angewendet")
        except Exception as e:
            logger.error(f"Fehler beim Anwenden der Einstellungen: {e}", exc_info=True)
        finally:
            self._applying_settings = False
    
    def apply_dpms_settings(self):
        """Wendet DPMS-Einstellungen (Display Power Management) an"""
        try:
            dpms_enabled = self.config.get('display.dpms_enabled', False)  # Default: False
            standby_minutes = self.config.get('display.dpms_standby_minutes', 0)  # Default: 0
            
            # Konvertiere Minuten in Sekunden für xset
            standby_seconds = standby_minutes * 60 if standby_minutes > 0 else 0
            
            # Setze DISPLAY-Umgebungsvariable
            display = os.environ.get('DISPLAY', ':0')
            env = os.environ.copy()
            env['DISPLAY'] = display
            
            if dpms_enabled and standby_seconds > 0:
                # Aktiviere DPMS und setze Standby-Zeit
                # xset dpms <standby> <suspend> <off>
                # Wir setzen nur Standby, suspend und off bleiben bei 0 (deaktiviert)
                cmd = ['xset', 'dpms', str(standby_seconds), '0', '0']
                result = subprocess.run(cmd, env=env, 
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    # Aktiviere DPMS explizit
                    subprocess.run(['xset', '+dpms'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    logger.info(f"DPMS aktiviert: Bildschirm schaltet nach {standby_minutes} Minuten aus ({standby_seconds}s)")
                else:
                    logger.warning(f"Fehler beim Aktivieren von DPMS: {result.stderr}")
            else:
                # Prüfe ob zeitgesteuerte Ein/Ausschaltung aktiv ist
                schedule_enabled = self.config.get('display.schedule_enabled', False)
                
                if schedule_enabled:
                    # Wenn Zeitsteuerung aktiv ist, DPMS aktiviert lassen (aber ohne Standby)
                    # Setze Standby auf sehr hohen Wert (24 Stunden), damit nur Zeitsteuerung greift
                    subprocess.run(['xset', 'dpms', '86400', '0', '0'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', '+dpms'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', 's', 'off'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', 's', 'noblank'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    logger.info("DPMS aktiviert für Zeitsteuerung (Standby: 24h), X11 Screensaver deaktiviert")
                else:
                    # Deaktiviere DPMS und X11 Screensaver komplett
                    subprocess.run(['xset', '-dpms'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', 's', 'off'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', 's', 'noblank'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    logger.info("DPMS und X11 Screensaver deaktiviert: Bildschirm bleibt immer an")
        except FileNotFoundError:
            logger.warning("xset nicht gefunden - DPMS kann nicht konfiguriert werden")
        except Exception as e:
            logger.error(f"Fehler beim Anwenden von DPMS-Einstellungen: {e}")
    
    def setup_display_schedule(self):
        """Richtet die zeitgesteuerte Ein/Ausschaltung des Bildschirms ein"""
        try:
            schedule_enabled = self.config.get('display.schedule_enabled', False)
            
            if hasattr(self, 'display_schedule_timer'):
                self.display_schedule_timer.stop()
                self.display_schedule_timer.deleteLater()
            
            if schedule_enabled:
                # Timer, der jede Minute prüft
                self.display_schedule_timer = QTimer()
                self.display_schedule_timer.timeout.connect(self.check_display_schedule)
                self.display_schedule_timer.start(60000)  # Jede Minute prüfen
                # Sofort prüfen
                self.check_display_schedule()
                logger.info("Zeitgesteuerte Bildschirm-Ein/Ausschaltung aktiviert")
            else:
                logger.info("Zeitgesteuerte Bildschirm-Ein/Ausschaltung deaktiviert")
        except Exception as e:
            logger.error(f"Fehler beim Einrichten der Zeitsteuerung: {e}")
    
    def check_display_schedule(self):
        """Prüft ob der Bildschirm ein- oder ausgeschaltet werden soll"""
        try:
            schedule_enabled = self.config.get('display.schedule_enabled', False)
            if not schedule_enabled:
                return
            
            on_time_str = self.config.get('display.schedule_on_time', '08:00')
            off_time_str = self.config.get('display.schedule_off_time', '22:00')
            
            if not on_time_str or not off_time_str:
                return
            
            # Parse Uhrzeiten
            on_hour, on_minute = map(int, on_time_str.split(':'))
            off_hour, off_minute = map(int, off_time_str.split(':'))
            
            # Aktuelle Zeit
            now = datetime.now()
            current_hour = now.hour
            current_minute = now.minute
            current_time_minutes = current_hour * 60 + current_minute
            on_time_minutes = on_hour * 60 + on_minute
            off_time_minutes = off_hour * 60 + off_minute
            
            # Setze DISPLAY-Umgebungsvariable
            display = os.environ.get('DISPLAY', ':0')
            env = os.environ.copy()
            env['DISPLAY'] = display
            
            # Prüfe ob Bildschirm ein- oder ausgeschaltet werden soll
            if on_time_minutes == off_time_minutes:
                # Gleiche Zeiten = deaktiviert
                return
            
            # Prüfe ob wir im Zeitfenster zwischen Einschalt- und Ausschaltzeit sind
            if on_time_minutes < off_time_minutes:
                # Normaler Fall: z.B. 08:00 - 22:00
                should_be_on = on_time_minutes <= current_time_minutes < off_time_minutes
            else:
                # Über Mitternacht: z.B. 22:00 - 08:00
                should_be_on = current_time_minutes >= on_time_minutes or current_time_minutes < off_time_minutes
            
            # Für zeitgesteuerte Ein/Ausschaltung muss DPMS aktiviert sein
            # Aktiviere DPMS temporär, falls es deaktiviert ist
            subprocess.run(['xset', '+dpms'], env=env, 
                         capture_output=True, text=True, timeout=5)
            
            # Prüfe aktuellen DPMS-Status, um unnötige Befehle zu vermeiden
            # Dies verhindert kurze schwarze Bildschirme, wenn der Bildschirm bereits an ist
            dpms_status = subprocess.run(['xset', 'q'], env=env, 
                                       capture_output=True, text=True, timeout=5)
            dpms_enabled = '+dpms' in dpms_status.stdout if dpms_status.returncode == 0 else False
            
            if should_be_on:
                # Bildschirm sollte an sein
                # Nur einschalten, wenn DPMS aktiviert ist (Bildschirm könnte aus sein)
                # Wenn DPMS deaktiviert ist, ist der Bildschirm bereits an
                if dpms_enabled:
                    # Prüfe ob Bildschirm wirklich aus ist, bevor wir einschalten
                    # Wenn DPMS aktiviert ist, können wir den Status nicht direkt prüfen
                    # Aber wir können vermeiden, force on zu verwenden, wenn nicht nötig
                    # Verwende stattdessen einen sanfteren Befehl
                    result = subprocess.run(['xset', 'dpms', 'force', 'on'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        logger.info(f"Bildschirm eingeschaltet (Zeitsteuerung: {on_time_str}-{off_time_str})")
                    else:
                        logger.warning(f"Fehler beim Einschalten des Bildschirms: {result.stderr}")
                else:
                    # DPMS ist deaktiviert, Bildschirm ist bereits an
                    logger.debug(f"Bildschirm bereits an (DPMS deaktiviert, Zeitsteuerung: {on_time_str}-{off_time_str})")
            else:
                # Bildschirm sollte aus sein
                if dpms_enabled:
                    subprocess.run(['xset', 'dpms', 'force', 'off'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    logger.info(f"Bildschirm ausgeschaltet (Zeitsteuerung: {on_time_str}-{off_time_str})")
                else:
                    # DPMS ist deaktiviert, schalte es ein und dann aus
                    subprocess.run(['xset', '+dpms'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    subprocess.run(['xset', 'dpms', 'force', 'off'], env=env, 
                                 capture_output=True, text=True, timeout=5)
                    logger.info(f"Bildschirm ausgeschaltet (Zeitsteuerung: {on_time_str}-{off_time_str})")
        except Exception as e:
            logger.error(f"Fehler beim Prüfen der Zeitsteuerung: {e}")
    
    def set_settings_queue(self, queue_obj):
        """Setzt die Settings-Queue und startet den Watcher"""
        self.settings_queue = queue_obj
        if self.settings_queue:
            self.settings_timer = QTimer()
            self.settings_timer.timeout.connect(self.check_settings_queue)
            self.settings_timer.start(1000)  # Prüfe jede Sekunde
            logger.info("Settings-Watcher gestartet")
    
    def check_settings_queue(self):
        """Prüft Queue auf Settings-Update-Signale"""
        if not self.settings_queue:
            return
        
        try:
            import queue
            while True:
                message = self.settings_queue.get_nowait()
                if message == 'reload_settings':
                    logger.info("Settings-Reload-Signal empfangen vom Webinterface")
                    self.apply_settings()
        except queue.Empty:
            pass
        except Exception as e:
            logger.error(f"Fehler beim Prüfen der Settings-Queue: {e}")
    
    def check_emails(self):
        """Prüft auf neue Emails"""
        imap_server = self.config.get('email.imap_server')
        username = self.config.get('email.username')
        password = self.config.get('email.password')
        
        if not all([imap_server, username, password]):
            return
        
        try:
            email_handler = EmailHandler(imap_server, 993, username, password)
            if email_handler.connect():
                download_dir = Path(self.config.get('paths.original_images'))
                downloaded = email_handler.check_for_new_images(download_dir)
                
                # Heruntergeladene Bilder verarbeiten
                processed_senders = set()
                proxy_dir = Path(self.config.get('paths.proxy_images'))
                metadata_file = proxy_dir / 'metadata.json'
                
                for image_path, sender, subject, date_str in downloaded:
                    if self.image_processor.is_supported(image_path):
                        # Bild verarbeiten
                        proxy_path = self.image_processor.process_image(image_path, proxy_dir)
                        logger.info(f"Bild verarbeitet: {image_path}")
                        
                        # EXIF-Daten extrahieren und speichern
                        exif_data = ExifExtractor.extract_all_exif(image_path)
                        
                        # Metadaten speichern
                        proxy_hash = proxy_path.stem
                        metadata = {}
                        if metadata_file.exists():
                            try:
                                with open(metadata_file, 'r', encoding='utf-8') as f:
                                    metadata = json.load(f)
                            except Exception as e:
                                logger.error(f"Fehler beim Laden der Metadaten: {e}")
                                metadata = {}
                        
                        metadata[proxy_hash] = {
                            'sender': sender,
                            'subject': subject,
                            'date': exif_data.get('date') or date_str,  # EXIF-Datum hat Priorität
                            'location': exif_data.get('location'),  # Stadt (Land)
                            'latitude': exif_data.get('latitude'),
                            'longitude': exif_data.get('longitude'),
                            'exif_data': exif_data  # Vollständige EXIF-Daten für Sortierung
                        }
                        
                        try:
                            metadata_file.parent.mkdir(parents=True, exist_ok=True)
                            with open(metadata_file, 'w', encoding='utf-8') as f:
                                json.dump(metadata, f, indent=2, ensure_ascii=False)
                            logger.info(f"Metadaten gespeichert für {proxy_hash}: sender={sender}, subject={subject[:30] if subject else ''}")
                            
                            # Playlists aktualisieren
                            playlist_manager = PlaylistManager(proxy_dir, metadata_file)
                            playlist_manager.add_image(proxy_hash)
                        except Exception as e:
                            logger.error(f"Fehler beim Speichern der Metadaten: {e}")
                        
                        processed_senders.add(sender)
                
                # Automatische Antworten senden
                if processed_senders and self.config.get('email.auto_reply', False):
                    reply_message = self.config.get('email.reply_message', 'Bild erfolgreich empfangen und zum Bilderrahmen hinzugefügt!')
                    for sender in processed_senders:
                        email_handler.send_reply(
                            recipient=sender,
                            subject='Bild empfangen - Picture Frame',
                            message=reply_message
                        )
                
                if downloaded:
                    self.slideshow.refresh()
                    self.slideshow_widget.refresh()
                
                email_handler.disconnect()
        except Exception as e:
            logger.error(f"Fehler beim Email-Check: {e}")

def main():
    """Hauptfunktion"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('pictureframe.log'),
            logging.StreamHandler()
        ]
    )
    
    app = QApplication(sys.argv)
    app.setApplicationName("Picture Frame")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()

