"""
EXIF-Daten-Extraktor für Picture Frame
Extrahiert EXIF-Daten (Datum, GPS, etc.) aus Bildern
"""
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class ExifExtractor:
    """Extrahiert EXIF-Daten aus Bildern"""
    
    @staticmethod
    def extract_all_exif(image_path: Path) -> Dict[str, Any]:
        """
        Extrahiert alle relevanten EXIF-Daten aus einem Bild
        
        Returns:
            Dictionary mit extrahierten Metadaten:
            - date: Erstellungsdatum (ISO-Format)
            - latitude: GPS-Breitengrad
            - longitude: GPS-Längengrad
            - location: Stadt und Land (falls verfügbar)
        """
        result = {
            'date': None,
            'latitude': None,
            'longitude': None,
            'location': None
        }
        
        try:
            with Image.open(image_path) as img:
                exif = img.getexif()
                if exif is None:
                    return result
                
                # 1. Erstellungsdatum extrahieren
                date = ExifExtractor._extract_date(exif)
                if date:
                    result['date'] = date
                
                # 2. GPS-Daten extrahieren
                gps_data = ExifExtractor._extract_gps(exif)
                if gps_data:
                    result['latitude'] = gps_data.get('latitude')
                    result['longitude'] = gps_data.get('longitude')
                    
                    # 3. Versuche Standort zu ermitteln (Reverse Geocoding)
                    if gps_data.get('latitude') and gps_data.get('longitude'):
                        location = ExifExtractor._get_location_from_gps(
                            gps_data['latitude'],
                            gps_data['longitude']
                        )
                        if location:
                            result['location'] = location
                
        except Exception:
            # Fehler beim Extrahieren von EXIF-Daten
            pass
        
        return result
    
    @staticmethod
    def _extract_date(exif) -> Optional[str]:
        """Extrahiert das Erstellungsdatum aus EXIF-Daten"""
        try:
            # 1. Prüfe Haupt-IFD (Tag 306: DateTime)
            if 306 in exif:
                try:
                    value = exif[306]
                    date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                    return date_obj.isoformat()
                except:
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
                        except:
                            pass
                    # DateTimeDigitized (Tag 36868) als Fallback
                    elif 36868 in exif_ifd:
                        try:
                            value = exif_ifd[36868]
                            date_obj = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
                            return date_obj.isoformat()
                        except:
                            pass
                except:
                    pass
        except Exception:
            # Fehler beim Extrahieren des Datums
            pass
        
        return None
    
    @staticmethod
    def _extract_gps(exif) -> Optional[Dict[str, float]]:
        """Extrahiert GPS-Koordinaten aus EXIF-Daten"""
        try:
            # GPS-IFD ist normalerweise bei Tag 34853
            if 34853 in exif:
                try:
                    gps_ifd = exif.get_ifd(34853)
                    gps_data = {}
                    
                    # GPS-Latitude (Tag 2)
                    if 2 in gps_ifd:
                        lat_ref = gps_ifd.get(1, 'N')  # GPSLatitudeRef
                        lat = ExifExtractor._convert_to_degrees(gps_ifd[2])
                        if lat_ref == 'S':
                            lat = -lat
                        gps_data['latitude'] = lat
                    
                    # GPS-Longitude (Tag 4)
                    if 4 in gps_ifd:
                        lon_ref = gps_ifd.get(3, 'E')  # GPSLongitudeRef
                        lon = ExifExtractor._convert_to_degrees(gps_ifd[4])
                        if lon_ref == 'W':
                            lon = -lon
                        gps_data['longitude'] = lon
                    
                    if gps_data:
                        return gps_data
                except Exception:
                    # Fehler beim Lesen von GPS-Daten
                    pass
        except Exception:
            # Fehler beim Extrahieren von GPS-Daten
            pass
        
        return None
    
    @staticmethod
    def _convert_to_degrees(value) -> float:
        """Konvertiert GPS-Koordinaten im EXIF-Format zu Dezimalgrad"""
        try:
            if isinstance(value, tuple):
                d, m, s = value
                return float(d) + float(m) / 60.0 + float(s) / 3600.0
            return float(value)
        except:
            return 0.0
    
    @staticmethod
    def _get_location_from_gps(latitude: float, longitude: float) -> Optional[str]:
        """
        Ermittelt Stadt und Land aus GPS-Koordinaten (Reverse Geocoding)
        Verwendet Nominatim (OpenStreetMap) API - kostenlos, aber Rate-Limited
        """
        try:
            import urllib.request
            import urllib.parse
            import json
            
            # Nominatim API (kostenlos, aber Rate-Limited: max 1 Request/Sekunde)
            url = f"https://nominatim.openstreetmap.org/reverse?lat={latitude}&lon={longitude}&format=json&addressdetails=1"
            
            # User-Agent ist erforderlich
            req = urllib.request.Request(url, headers={'User-Agent': 'PictureFrame/1.0'})
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                
                if 'address' in data:
                    address = data['address']
                    city = address.get('city') or address.get('town') or address.get('village') or address.get('municipality')
                    country = address.get('country')
                    
                    if city and country:
                        return f"{city} ({country})"
                    elif city:
                        return city
                    elif country:
                        return country
        except Exception:
            # Fehler beim Reverse Geocoding
            pass
        
        return None

