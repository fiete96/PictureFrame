"""
Email-Handler für Picture Frame
Empfängt Bilder per Email und verarbeitet sie
"""
import imapclient
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from pathlib import Path
import logging
from typing import List, Tuple, Optional
import ssl

logger = logging.getLogger(__name__)

class EmailHandler:
    def __init__(self, server: str, port: int, username: str, password: str):
        self.server = server
        self.port = port
        self.username = username
        self.password = password
        self.client = None
    
    def connect(self) -> bool:
        """Verbindet zum IMAP-Server"""
        try:
            ssl_context = ssl.create_default_context()
            self.client = imapclient.IMAPClient(self.server, port=self.port, ssl=True, ssl_context=ssl_context)
            self.client.login(self.username, self.password)
            self.client.select_folder('INBOX')
            logger.info("Erfolgreich mit Email-Server verbunden")
            return True
        except Exception as e:
            logger.error(f"Fehler beim Verbinden mit Email-Server: {e}")
            return False
    
    def disconnect(self):
        """Trennt die Verbindung zum IMAP-Server"""
        if self.client:
            try:
                self.client.logout()
            except:
                pass
            self.client = None
    
    def check_for_new_images(self, download_dir: Path) -> List[Tuple[Path, str, str, str]]:
        """
        Prüft auf neue Emails mit Bildern
        
        Returns:
            Liste von Tupeln (Dateipfad, Absender-Email, Betreff, Datum)
        """
        if not self.client:
            if not self.connect():
                return []
        
        downloaded_files = []
        
        try:
            # Suche nach ungelesenen Emails
            messages = self.client.search(['UNSEEN'])
            
            for msg_id in messages:
                try:
                    response = self.client.fetch([msg_id], ['RFC822', 'ENVELOPE'])
                    msg_data = response[msg_id]
                    
                    # Email parsen
                    email_body = msg_data[b'RFC822']
                    msg = email.message_from_bytes(email_body)
                    
                    # Absender extrahieren
                    sender = self._get_sender(msg)
                    
                    # Betreff extrahieren
                    subject = self._get_subject(msg)
                    
                    # Datum extrahieren
                    date_str = self._get_date(msg)
                    
                    # Anhänge durchsuchen
                    attachments = self._extract_attachments(msg, download_dir, sender, subject, date_str)
                    
                    # Wenn Bilder gefunden wurden, Email löschen
                    if attachments:
                        downloaded_files.extend(attachments)
                        # Email löschen (statt nur als gelesen markieren)
                        self.client.delete_messages([msg_id])
                        logger.info(f"Email {msg_id} gelöscht nach erfolgreichem Download von {len(attachments)} Bild(ern)")
                    else:
                        # Keine Bilder gefunden - Email als gelesen markieren
                        self.client.set_flags([msg_id], [imapclient.SEEN])
                    
                except Exception as e:
                    logger.error(f"Fehler beim Verarbeiten von Email {msg_id}: {e}")
                    continue
            
            # Lösche markierte Emails (expunge)
            if downloaded_files:
                try:
                    self.client.expunge()
                    logger.info("Gelöschte Emails wurden vom Server entfernt")
                except Exception as e:
                    logger.warning(f"Fehler beim Entfernen gelöschter Emails: {e}")
            
            logger.info(f"{len(downloaded_files)} neue Bilder heruntergeladen")
            return downloaded_files
            
        except Exception as e:
            logger.error(f"Fehler beim Prüfen auf neue Emails: {e}")
            return downloaded_files
    
    def _get_sender(self, msg: email.message.Message) -> str:
        """Extrahiert die Absender-Email-Adresse"""
        sender = msg.get('From', '')
        if '<' in sender and '>' in sender:
            start = sender.find('<') + 1
            end = sender.find('>')
            sender = sender[start:end]
        return sender.strip()
    
    def _get_subject(self, msg: email.message.Message) -> str:
        """Extrahiert den Betreff der Email"""
        subject = msg.get('Subject', '')
        if subject:
            try:
                decoded_parts = decode_header(subject)
                decoded_subject = ""
                for part, encoding in decoded_parts:
                    if isinstance(part, bytes):
                        decoded_subject += part.decode(encoding or 'utf-8')
                    else:
                        decoded_subject += part
                return decoded_subject.strip()
            except:
                return subject.strip()
        return ''
    
    def _get_date(self, msg: email.message.Message) -> str:
        """Extrahiert das Datum der Email"""
        date_str = msg.get('Date', '')
        if date_str:
            try:
                from email.utils import parsedate_to_datetime
                date_obj = parsedate_to_datetime(date_str)
                return date_obj.isoformat()
            except:
                return date_str
        return datetime.now().isoformat()
    
    def _extract_attachments(self, msg: email.message.Message, download_dir: Path, sender: str, subject: str, date_str: str) -> List[Tuple[Path, str, str, str]]:
        """Extrahiert Bild-Anhänge aus der Email"""
        downloaded_files = []
        download_dir.mkdir(parents=True, exist_ok=True)
        
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))
            
            if "attachment" in content_disposition or part.get_content_type().startswith('image/'):
                try:
                    filename = part.get_filename()
                    if filename:
                        # Dateinamen dekodieren
                        filename = self._decode_filename(filename)
                        
                        # Nur Bilddateien verarbeiten
                        if self._is_image_file(filename):
                            filepath = download_dir / filename
                            
                            # Falls Datei bereits existiert, Nummer anhängen
                            counter = 1
                            original_filepath = filepath
                            while filepath.exists():
                                stem = original_filepath.stem
                                suffix = original_filepath.suffix
                                filepath = download_dir / f"{stem}_{counter}{suffix}"
                                counter += 1
                            
                            # Datei speichern
                            with open(filepath, 'wb') as f:
                                f.write(part.get_payload(decode=True))
                            
                            logger.info(f"Bild heruntergeladen: {filepath}")
                            downloaded_files.append((filepath, sender, subject, date_str))
                            
                except Exception as e:
                    logger.error(f"Fehler beim Extrahieren des Anhangs: {e}")
        
        return downloaded_files
    
    def _decode_filename(self, filename: str) -> str:
        """Dekodiert den Dateinamen aus Email-Header"""
        try:
            decoded_parts = decode_header(filename)
            decoded_filename = ""
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    decoded_filename += part.decode(encoding or 'utf-8')
                else:
                    decoded_filename += part
            return decoded_filename
        except:
            return filename
    
    def _is_image_file(self, filename: str) -> bool:
        """Prüft, ob es sich um eine Bilddatei handelt"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif'}
        return Path(filename).suffix.lower() in image_extensions
    
    def send_reply(self, recipient: str, subject: str, message: str, smtp_server: Optional[str] = None, smtp_port: int = 587) -> bool:
        """
        Sendet eine Antwort-Email
        
        Args:
            recipient: Empfänger-Email-Adresse
            subject: Betreff
            message: Nachrichtentext
            smtp_server: SMTP-Server (falls anders als IMAP-Server)
            smtp_port: SMTP-Port
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        try:
            # SMTP-Server bestimmen
            if smtp_server is None:
                # Versuche SMTP-Server aus IMAP-Server abzuleiten
                if 'gmail' in self.server.lower():
                    smtp_server = 'smtp.gmail.com'
                elif 'outlook' in self.server.lower() or 'hotmail' in self.server.lower():
                    smtp_server = 'smtp-mail.outlook.com'
                elif 'yahoo' in self.server.lower():
                    smtp_server = 'smtp.mail.yahoo.com'
                else:
                    # Versuche smtp. vor IMAP-Server zu setzen
                    smtp_server = self.server.replace('imap.', 'smtp.').replace('imap', 'smtp')
            
            # Email erstellen
            msg = MIMEMultipart()
            msg['From'] = self.username
            msg['To'] = recipient
            msg['Subject'] = subject
            
            msg.attach(MIMEText(message, 'plain', 'utf-8'))
            
            # Verbindung herstellen und senden
            if smtp_port == 465:
                # SSL-Verbindung
                server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                # STARTTLS
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
            
            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()
            
            logger.info(f"Antwort-Email an {recipient} gesendet")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Senden der Antwort-Email: {e}")
            return False

