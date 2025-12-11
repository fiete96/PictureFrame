"""
Konfigurationsmanager für Picture Frame
"""
import yaml
import os
from pathlib import Path
from typing import Dict, Any

class ConfigManager:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._ensure_directories()
    
    def _load_config(self) -> Dict[str, Any]:
        """Lädt die Konfiguration aus der YAML-Datei"""
        default = self._default_config()
        if self.config_path.exists():
            with open(self.config_path, 'r', encoding='utf-8') as f:
                loaded = yaml.safe_load(f) or {}
                # Merge mit Default-Config, um fehlende Werte zu ergänzen
                return self._merge_config(default, loaded)
        return default
    
    def _merge_config(self, default: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
        """Führt geladene Config mit Default-Config zusammen (rekursiv)"""
        result = default.copy()
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Rekursiv mergen für verschachtelte Dictionaries
                result[key] = self._merge_config(result[key], value)
            else:
                # Wert überschreiben oder hinzufügen
                result[key] = value
        return result
    
    def _default_config(self) -> Dict[str, Any]:
        """Standard-Konfiguration"""
        return {
            'display': {
                'width': 1024,
                'height': 600,
                'fullscreen': True,
                'dpms_enabled': False,  # Default: deaktiviert
                'dpms_standby_minutes': 0,  # Default: 0 (deaktiviert)
                'schedule_enabled': False,  # Zeitgesteuerte Ein/Ausschaltung
                'schedule_on_time': '08:00',  # Uhrzeit zum Einschalten (HH:MM)
                'schedule_off_time': '22:00'  # Uhrzeit zum Ausschalten (HH:MM)
            },
            'slideshow': {
                'auto_play': True,  # Automatische Slideshow aktiviert
                'interval_seconds': 10,
                'transition_duration': 1.0,
                'shuffle': False,  # Deprecated - wird durch sort_by ersetzt
                'sort_by': 'transfer_time',  # Sortierung: transfer_time, creation_time, random
                'loop': True
            },
            'email': {
                'imap_server': '',
                'imap_port': 993,
                'username': '',
                'password': '',
                'check_interval_minutes': 5,
                'auto_reply': True,
                'reply_message': 'Bild erfolgreich empfangen und zum Bilderrahmen hinzugefügt!'
            },
            'paths': {
                'original_images': './images/originals',
                'proxy_images': './images/proxies',
                'temp': './temp'
            },
            'web': {
                'host': '0.0.0.0',
                'port': 80,
                'debug': False
            },
            'wifi': {
                'ssid': '',
                'password': ''
            }
        }
    
    def _ensure_directories(self):
        """Stellt sicher, dass alle benötigten Verzeichnisse existieren"""
        paths = self.config.get('paths', {})
        for key, path in paths.items():
            Path(path).mkdir(parents=True, exist_ok=True)
    
    def get(self, key_path: str, default=None):
        """Holt einen Wert aus der Konfiguration mit Punkt-Notation"""
        keys = key_path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
                if value is None:
                    return default
            else:
                return default
        return value
    
    def set(self, key_path: str, value: Any):
        """Setzt einen Wert in der Konfiguration mit Punkt-Notation"""
        keys = key_path.split('.')
        config = self.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        self.save()
    
    def save(self):
        """Speichert die Konfiguration in die YAML-Datei"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
    
    def get_all(self) -> Dict[str, Any]:
        """Gibt die komplette Konfiguration zurück"""
        return self.config.copy()

