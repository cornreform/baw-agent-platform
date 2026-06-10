"""
BAW — Telegram Bot Connector

Long-polling Telegram Bot via httpx (no extra dependencies).
Fully featured: commands, replies, error handling, reconnection.
"""
from __future__ import annotations
import json
import logging
import threading
import time
import httpx
from typing import Optional

from . import BaseConnector, Message, register

logger = logging.getLogger("baw.telegram")

POLL_TIMEOUT = 30  # Long-poll timeout (seconds)
POLL_RETRY_DELAY = 5
MAX_MESSAGE_LENGTH = 4000
API_BASE = "https://api.telegram.org/bot{token}"


@register("telegram", "Telegram Bot — long-polling via httpx", "telegram")
class TelegramConnector(BaseConnector):
    """Telegram Bot connector using long-polling (getUpdates).

    Config:
      telegram:
        token: "***"          # Bot token from @BotFather
        allowed_users: []     # Optional: list of user IDs to allow
    """

    def __init__(self, config: dict, on_message):
        super().__init__(config.get("telegram", {}), on_message)
        self._token = self.config.get("token", "")
        self._allowed = self.config.get("allowed_users", [])
        self._offset = 0
        self._client: httpx.Client | None = None
        self._api_base = API_BASE.format(token=self._token) if self._token else ""
        self._debounce_until = 0.0
        self._restart_chat_id: str | None = None
        self._tts_enabled = self.config.get("tts_enabled", False)
        self._tts_voice = self.config.get("tts_voice", "male-tone-1")
        self._selector_msg_id: int | None = None
        self._selector_role: dict[str, str] = {}  # {chat_id: role} for 3-layer model selector
        # ── Media group buffering ──
        self._media_group_buffers: dict[str, dict[str, list]] = {}  # {chat_id: {group_id: [msgs]}}
        self._media_group_timers: dict[str, threading.Timer] = {}
        self._media_group_wait = 2.5  # seconds to wait for all media in a group

    def connect(self) -> bool:
        """Test connection by fetching bot info."""
        if not self._token:
            logger.error("[Telegram] No token configured")
            return False
        try:
            self._client = httpx.Client(timeout=10)
            r = self._client.get(f"{self._api_base}/getMe")
            if r.status_code == 200:
                info = r.json()
                if info.get("ok"):
                    bot_name = info["result"].get("first_name", "BAW Bot")
                    logger.info(f"[Telegram] Connected as @{info['result'].get('username', '?')}")

                    # Register slash command menu
                    self._register_commands()

                    # ── Back-online notification after restart ──
                    self._notify_restart()

                    return True
            logger.error(f"[Telegram] getMe failed: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"[Telegram] Connection error: {e}")
            return False

    def _notify_restart(self):
        """Send 'Back Online' notification if this was a restart."""
        import json as _json
        from pathlib import Path
        pending_file = Path.home() / ".baw" / ".restart_pending"
        if not pending_file.exists():
            return
        try:
            data = _json.loads(pending_file.read_text())
            chat_id = data.get("chat_id", "")
            if chat_id:
                self._client.post(
                    f"{self._api_base}/sendMessage",
                    json={"chat_id": chat_id, "text": "✅ **BAW Back Online**"},
                    timeout=10,
                )
                logger.info(f"[Telegram] Restart notification sent to {chat_id}")
            pending_file.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"[Telegram] Restart notification failed: {e}")

    def _register_commands(self):
        """Register bot command menu via setMyCommands."""
        commands = [
            {"command": "start",   "description": "Welcome message"},
            {"command": "help",    "description": "Show all commands"},
            {"command": "status",  "description": "BAW system status + sessions"},
            {"command": "btw",     "description": "Quick answer (no court)"},
            {"command": "model",   "description": "Switch model or show model selector"},
            {"command": "mode",    "description": "Switch mode: quick / hybrid / tight"},
            {"command": "tone",    "description": "Switch tone: casual / business / teaching"},
            {"command": "set",     "description": "Persist config: /set key value"},
            {"command": "court",   "description": "Show last Angel/Devil verdict"},
            {"command": "fresh",   "description": "Raw model — no soul, no memories"},
            {"command": "memory",  "description": "Save a memory entry"},
            {"command": "search",  "description": "Search stored memories"},
            {"command": "board",   "description": "Generate HTML dashboard"},
            {"command": "task",    "description": "Session: new/list/resume/save/forget/info"},
            {"command": "list",    "description": "List saved sessions (alias for /task list)"},
            {"command": "new",     "description": "Save current + start fresh session"},
            {"command": "reset",   "description": "Hard reset — clear session without saving"},
            {"command": "resume",  "description": "Resume a saved session"},
            {"command": "summarize", "description": "LLM summary of current session"},
            {"command": "pickup",  "description": "Resume last interrupted session"},
            {"command": "reload",  "description": "Hot-reload tools & config"},
            {"command": "evolve",  "description": "Self-evolution stats"},
            {"command": "tts",     "description": "Toggle TTS: on / off / status"},
            {"command": "capability", "description": "Manage capabilities"},
            {"command": "update",  "description": "Git pull + changelog + restart"},
            {"command": "stop",    "description": "Cancel running request"},
            {"command": "restart", "description": "Restart BAW engine"},
        ]
        try:
            self._client.post(
                f"{self._api_base}/setMyCommands",
                json={"commands": commands},
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"[Telegram] Failed to register commands: {e}")

    def disconnect(self):
        if self._client:
            self._client.close()
            self._client = None

    def send(self, chat_id: str, text: str, edit_msg_id: str = "") -> str:
        """Send or edit a message. Returns message_id if successful, empty string if failed.
        If edit_msg_id is provided, edits that message instead of sending new one."""
        if not self._client or not self._token:
            return ""
        # ── Intercept model selector ——
        if text.startswith("[MODEL_ROLE_SELECT]\n"):
            self._send_role_selector(chat_id, text)
            return ""
        if text.startswith("[MODEL_SELECT]\n"):
            self._send_model_selector_text(chat_id, text)
            return ""
        try:
            # ── Extract MEDIA: tags ──
            import re as _re
            media_files = _re.findall(r'^MEDIA:(.+)$', text, _re.MULTILINE)
            if media_files:
                text = _re.sub(r'^MEDIA:.+$\n?', '', text, flags=_re.MULTILINE).strip()
                # Can't edit media — send new message
                edit_msg_id = ""

            # ── Edit existing message ──
            if edit_msg_id:
                if text:
                    return self._edit_text(chat_id, edit_msg_id, text)
                return edit_msg_id

            # ── Send new text ──
            msg_id = ""
            if text:
                if len(text) > MAX_MESSAGE_LENGTH:
                    parts = []
                    remaining = text
                    while remaining:
                        parts.append(remaining[:MAX_MESSAGE_LENGTH])
                        remaining = remaining[MAX_MESSAGE_LENGTH:]
                    for part in parts:
                        msg_id = self._send_text(chat_id, part)
                else:
                    msg_id = self._send_text(chat_id, text)

            # ── Send media files ──
            for fpath in media_files:
                fpath = fpath.strip()
                self._send_media(chat_id, fpath)

            return msg_id or ""
        except Exception as e:
            logger.error(f"[Telegram] send error: {e}")
            return ""

    def _send_text(self, chat_id: str, text: str) -> str:
        """Send a plain text message. Returns message_id string or empty on failure."""
        r = self._client.post(
            f"{self._api_base}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            if "can't parse entities" in r.text:
                r = self._client.post(
                    f"{self._api_base}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                    timeout=10,
                )
        if r.status_code == 200:
            data = r.json()
            if data.get("ok"):
                return str(data["result"]["message_id"])
        return ""

    def _edit_text(self, chat_id: str, message_id: str, text: str) -> str:
        """Edit an existing message. Returns message_id on success, empty on failure."""
        r = self._client.post(
            f"{self._api_base}/editMessageText",
            json={"chat_id": chat_id, "message_id": int(message_id), "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if r.status_code != 200:
            if "can't parse entities" in r.text:
                r = self._client.post(
                    f"{self._api_base}/editMessageText",
                    json={"chat_id": chat_id, "message_id": int(message_id), "text": text},
                    timeout=10,
                )
        if r.status_code == 200:
            return message_id
        return ""

    def send_typing(self, chat_id: str) -> bool:
        """Send typing indicator to show '...' in Telegram."""
        if not self._client or not self._token:
            return False
        try:
            r = self._client.post(
                f"{self._api_base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    _MEDIA_EXT_MAP = {
        ".png": "sendPhoto", ".jpg": "sendPhoto", ".jpeg": "sendPhoto",
        ".webp": "sendPhoto", ".gif": "sendPhoto",
        ".mp4": "sendVideo", ".mov": "sendVideo",
        ".mp3": "sendAudio", ".wav": "sendAudio",
        ".ogg": "sendVoice",
    }

    def _send_media(self, chat_id: str, file_path: str):
        """Send a media file using the appropriate Telegram API endpoint."""
        from pathlib import Path

        fpath = Path(file_path).expanduser()
        if not fpath.exists():
            logger.warning(f"[Telegram] MEDIA file not found: {fpath}")
            self._send_text(chat_id, f"File not found: {fpath}")
            return

        ext = fpath.suffix.lower()
        endpoint = self._MEDIA_EXT_MAP.get(ext, "sendDocument")

        field_map = {
            "sendPhoto": "photo", "sendDocument": "document",
            "sendVideo": "video", "sendAudio": "audio", "sendVoice": "voice",
        }
        field = field_map.get(endpoint, "document")

        try:
            with open(fpath, "rb") as f:
                files = {field: (fpath.name, f)}
                r = self._client.post(
                    f"{self._api_base}/{endpoint}",
                    data={"chat_id": chat_id},
                    files=files,
                    timeout=60,
                )
            if r.status_code != 200:
                logger.warning(f"[Telegram] Media send failed ({endpoint}): {r.text[:200]}")
                if endpoint != "sendDocument":
                    self._send_media_as_document(chat_id, fpath)
        except Exception as e:
            logger.error(f"[Telegram] Media send error ({fpath}): {e}")

    def _send_media_as_document(self, chat_id: str, fpath):
        """Fallback: send any file as a document."""
        from pathlib import Path
        fpath = Path(fpath)
        if not fpath.exists():
            return
        try:
            with open(fpath, "rb") as f:
                self._client.post(
                    f"{self._api_base}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": (fpath.name, f)},
                    timeout=60,
                )
        except Exception as e:
            logger.error(f"[Telegram] Document fallback error ({fpath}): {e}")

    # ── File processing ─────────────────────────────────────────

    def _download_file(self, file_id: str, file_name: str = "file") -> str:
        """Download a file from Telegram Bot API. Returns local path."""
        import os
        from pathlib import Path

        # Get file path from Telegram
        r = self._client.post(
            f"{self._api_base}/getFile",
            json={"file_id": file_id},
            timeout=10,
        )
        if r.status_code != 200:
            raise RuntimeError(f"getFile failed: {r.text[:200]}")
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile returned error: {data}")
        tg_path = data["result"]["file_path"]

        # Download file
        dl_url = f"https://api.telegram.org/file/bot{self._token}/{tg_path}"
        dl_r = self._client.get(dl_url, timeout=120)
        if dl_r.status_code != 200:
            raise RuntimeError(f"File download failed: {dl_r.status_code}")

        # Save to temp
        tmp_dir = Path.home() / ".baw" / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        local_path = tmp_dir / file_name
        # Avoid collisions
        if local_path.exists():
            base = local_path.stem
            ext = local_path.suffix
            i = 1
            while local_path.exists():
                local_path = tmp_dir / f"{base}_{i}{ext}"
                i += 1
        local_path.write_bytes(dl_r.content)
        logger.info(f"[Telegram] Downloaded: {tg_path} → {local_path} ({len(dl_r.content)} bytes)")
        return str(local_path)

    def _extract_file_content(self, file_path: str) -> str:
        """Extract text content from a file based on extension. Returns str."""
        from pathlib import Path

        fp = Path(file_path)
        ext = fp.suffix.lower()
        name = fp.name

        # ── Plain text ──
        if ext in (".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
                    ".log", ".ini", ".cfg", ".conf", ".toml", ".py",
                    ".js", ".ts", ".html", ".css", ".sh", ".bash"):
            try:
                return fp.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return fp.read_text(encoding="latin-1")

        # ── PowerPoint ──
        elif ext in (".pptx", ".pptm"):
            try:
                from pptx import Presentation
                prs = Presentation(fp)
                parts = []
                for i, slide in enumerate(prs.slides, 1):
                    texts = []
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and shape.text.strip():
                            texts.append(shape.text.strip())
                    if texts:
                        parts.append(f"--- Slide {i} ---\n" + "\n".join(texts))
                return "\n\n".join(parts) if parts else "[No text found in slides]"
            except Exception as e:
                return f"[Error extracting PowerPoint: {e}]"

        # ── Word ──
        elif ext in (".docx", ".docm"):
            try:
                from docx import Document
                doc = Document(fp)
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                return "\n\n".join(paras) if paras else "[No text found in document]"
            except Exception as e:
                return f"[Error extracting Word: {e}]"

        # ── PDF ──
        elif ext == ".pdf":
            try:
                # Try PyMuPDF first (fastest)
                import fitz
                doc = fitz.open(fp)
                pages = []
                for i, page in enumerate(doc, 1):
                    text = page.get_text().strip()
                    if text:
                        pages.append(f"--- Page {i} ---\n{text}")
                doc.close()
                result = "\n\n".join(pages)
                if result.strip():
                    return result
                # Fallback: pdftotext if installed
                import subprocess as sp
                txt_path = fp.with_suffix(".txt")
                sp.run(["pdftotext", str(fp), str(txt_path)], capture_output=True, timeout=30)
                if txt_path.exists():
                    return txt_path.read_text(encoding="utf-8")
                return "[No text extracted from PDF]"
            except Exception as e:
                return f"[Error extracting PDF: {e}]"

        # ── Excel ──
        elif ext in (".xlsx", ".xlsm"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
                parts = []
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    rows = []
                    for row in ws.iter_rows(values_only=True):
                        vals = [str(v) if v is not None else "" for v in row]
                        line = " | ".join(vals)
                        if line.strip():
                            rows.append(line)
                    if rows:
                        parts.append(f"--- Sheet: {sheet_name} ---\n" + "\n".join(rows))
                wb.close()
                return "\n\n".join(parts) if parts else "[No data found in spreadsheet]"
            except Exception as e:
                return f"[Error extracting Excel: {e}]"

        # ── CSV (fallback for .csv not caught above) ──
        elif ext == ".csv":
            import csv, io
            try:
                text = fp.read_text(encoding="utf-8")
                reader = csv.reader(io.StringIO(text))
                lines = [" | ".join(row) for row in reader]
                return "\n".join(lines)
            except Exception as e:
                return f"[Error reading CSV: {e}]"

        # ── Images ──
        elif ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"):
            try:
                import pytesseract
                from PIL import Image
                img = Image.open(fp)
                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                if text.strip():
                    return f"[OCR extracted text from {name}]\n\n{text}"
                return f"[Image {name}: no text detected via OCR]"
            except Exception as e:
                return f"[Error processing image {name}: {e}]"

        # ── Unknown ──
        else:
            return f"[Unsupported file type: {ext}. Cannot extract content.]"

    # ─────────────────────────────────────────────────────────────
    #  Media group handling + non-text message queueing
    # ─────────────────────────────────────────────────────────────

    def _handle_media_msg(self, chat_id: str, user_id, user_name: str, msg: dict, msg_type: str):
        """Single non-text message: acquire slot or queue (never drop)."""
        if self._acquire_slot():
            self._cancel_event.clear()
            if msg_type == "photo":
                photo_data = max(msg.get("photo", []), key=lambda p: p.get("file_size", 0))
                threading.Thread(
                    target=self._process_image_file,
                    args=(chat_id, photo_data, msg),
                    daemon=True,
                ).start()
            elif msg_type == "document":
                threading.Thread(
                    target=self._process_document_file,
                    args=(chat_id, msg.get("document"), msg),
                    daemon=True,
                ).start()
            elif msg_type == "voice":
                voice_data = msg.get("audio") or msg.get("voice")
                threading.Thread(
                    target=self._process_voice_file,
                    args=(chat_id, voice_data, msg),
                    daemon=True,
                ).start()
        else:
            pos = self._enqueue_message(chat_id, user_id, user_name, "", msg, msg_type=msg_type)
            type_label = {"photo": "圖片", "document": "文件", "voice": "語音"}.get(msg_type, msg_type)
            self.send(chat_id, f"⏳ {type_label}已排隊 #{pos} — 處理完當前任務後自動開始")

    def _buffer_media_group(self, chat_id: str, group_id: str, msg: dict):
        """Buffer a media group message. Start/reset a timer; when it fires, process all."""
        if chat_id not in self._media_group_buffers:
            self._media_group_buffers[chat_id] = {}
        buf = self._media_group_buffers[chat_id]
        buf.setdefault(group_id, []).append(msg)
        count = len(buf[group_id])

        # Cancel existing timer for this group, start a new one
        timer_key = f"{chat_id}:{group_id}"
        if timer_key in self._media_group_timers:
            self._media_group_timers[timer_key].cancel()

        timer = threading.Timer(self._media_group_wait, self._process_media_group, args=[chat_id, group_id])
        timer.daemon = True
        self._media_group_timers[timer_key] = timer
        timer.start()

        # Determine type label for the notification
        is_photo = bool(msg.get("photo"))
        label = "📸 圖片" if is_photo else "📎 檔案"
        self.send(chat_id, f"{label} {count} 收到 — 等埋其他同組檔案...")

    def _process_media_group(self, chat_id: str, group_id: str):
        """Timer fired — all media in this group should have arrived. Queue them all."""
        msgs = self._media_group_buffers.get(chat_id, {}).pop(group_id, [])
        timer_key = f"{chat_id}:{group_id}"
        self._media_group_timers.pop(timer_key, None)

        if not msgs:
            return

        total = len(msgs)
        is_photo = bool(msgs[0].get("photo"))
        label = "📸 圖片" if is_photo else "📎 檔案"
        self.send(chat_id, f"{label}組共 {total} 個 — 開始排隊處理...")

        for msg in msgs:
            user_id = str(msg["from"]["id"])
            user_name = msg["from"].get("first_name", "User")
            msg_type = "photo" if msg.get("photo") else "document"
            self._handle_media_msg(chat_id, user_id, user_name, msg, msg_type)

    # ─────────────────────────────────────────────────────────────
    #  File processors
    # ─────────────────────────────────────────────────────────────

    def _process_document_file(self, chat_id: str, doc: dict, msg: dict):
        """Download, extract, and analyze a document via BAW. Inline edit — one message."""
        try:
            file_id = doc["file_id"]
            file_name = doc.get("file_name", "document")

            status_id = self.send(chat_id, f"📥 Downloading **{file_name}**...")

            local_path = self._download_file(file_id, file_name)
            self.send(chat_id, f"🔍 Extracting content...", edit_msg_id=status_id)

            content = self._extract_file_content(local_path)

            prompt = (
                f"[File: {file_name}]\n"
                f"[Type: {doc.get('mime_type', 'unknown')}]\n\n"
                f"{content}\n\n"
                f"---\n"
                f"Analyze this file. "
                f"Summarize its key content in Traditional Chinese. "
                f"If it's a technical document, identify the main topics."
            )

            self.send(chat_id, f"🤔 Analyzing with BAW...", edit_msg_id=status_id)
            response = self._run_baw(prompt, chat_id=chat_id)
            try:
                self._client.post(
                    f"{self._api_base}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": int(status_id)},
                    timeout=5,
                )
            except Exception:
                pass
            self.send(chat_id, response)
            self._record_batch_result(chat_id, response[:200], "document")

        except Exception as e:
            logger.error(f"[Telegram] Document processing error: {e}")
            self.send(chat_id, f"❌ Error processing document: {e}")
        finally:
            self._release_slot()

    def _process_image_file(self, chat_id: str, photo_data: dict, msg: dict):
        """Download an image and analyze with MiniMax vision (not OCR). Inline edit — one message."""
        try:
            file_id = photo_data["file_id"]
            file_name = f"photo_{file_id[:8]}.jpg"

            # ── Single inline-edited status message ──
            status_id = self.send(chat_id, "📥 Downloading image...")

            local_path = self._download_file(file_id, file_name)

            import subprocess as sp
            self.send(chat_id, "👁️ Analyzing with vision...", edit_msg_id=status_id)
            try:
                r = sp.run(
                    ["mmx", "vision", "describe", local_path,
                     "--question", "Describe this image in detail. What objects, text, brands, or products do you see? If it's a product, identify it and suggest where to buy it."],
                    capture_output=True, text=True, timeout=60,
                )
                vision_result = r.stdout.strip() or r.stderr.strip() or "(vision returned nothing)"
            except sp.TimeoutExpired:
                vision_result = "(vision timeout)"
            except FileNotFoundError:
                content = self._extract_file_content(local_path)
                vision_result = f"OCR: {content}"

            prompt = (
                f"[Image analysis via MiniMax vision]\n"
                f"File: {file_name}\n\n"
                f"Vision result:\n{vision_result}\n\n"
                f"---\n"
                f"Based on the vision analysis above, answer in Traditional Chinese:\n"
                f"- What is shown in this image?\n"
                f"- If it's a product: what is it, and where can I buy it?\n"
                f"- If there are similar items: suggest alternatives."
            )

            self.send(chat_id, "🤔 Analyzing with BAW...", edit_msg_id=status_id)
            response = self._run_baw(prompt, chat_id=chat_id)
            # Delete the status message, send clean result
            try:
                self._client.post(
                    f"{self._api_base}/deleteMessage",
                    json={"chat_id": chat_id, "message_id": int(status_id)},
                    timeout=5,
                )
            except Exception:
                pass
            self.send(chat_id, response)
            self._record_batch_result(chat_id, response[:200], "image")

        except Exception as e:
            logger.error(f"[Telegram] Image processing error: {e}")
            self.send(chat_id, f"❌ Error processing image: {e}")
        finally:
            self._release_slot()

    def _process_voice_file(self, chat_id: str, voice_data: dict, msg: dict):
        """Download audio, check STT capability, then transcribe or present options."""
        import os
        text = ""
        info = None
        try:
            file_id = voice_data["file_id"]
            ext = ".ogg" if msg.get("voice") else ".mp3"
            self.send(chat_id, "📥 下載語音...")

            local_path = self._download_file(file_id, f"voice_{file_id[:8]}{ext}")

            # ── Step 1: Load BAW config and check STT capability ──
            baw = self._baw_ensure()
            config = baw["config"]
            default_model_id = config.get("model", {}).get("default", "")
            all_models = self._scan_all_models(config)

            # Current model capability
            current_model = next((m for m in all_models if m["id"] == default_model_id), None)
            audio_models = [m for m in all_models if m.get("audio_input")]

            # ── Step 2: Check STT capability ──
            stt_config = config.get("capabilities", {}).get("stt", {})
            stt_method = stt_config.get("method", "").strip()

            # Check if any model has "stt" capability
            stt_models = [m for m in all_models if "stt" in m.get("capabilities", [])]

            # ── Step 3: Check if faster-whisper is installed ──
            fw_available = False
            try:
                import faster_whisper as _fw
                fw_available = True
            except ImportError:
                pass

            # ── Capability message parts ──
            status_lines = []
            used_method = None

            # Priority A: Model native audio_input
            if current_model and current_model.get("audio_input"):
                status_lines.append(f"✅ 主模型 **{default_model_id}** 支援音訊輸入（audio_input=true）")
                # Native audio handling via model API — currently falls through
                # to configured STT methods or installed fallbacks below.
                # Future: implement native audio API call per model here.

            # Priority B: Config-defined STT method or model
            if not used_method and stt_method:
                status_lines.append(f"⚙️ STT 配置 method：**{stt_method}**")
                if stt_method == "faster-whisper" and fw_available:
                    fw_model = stt_config.get("model", "base")
                    status_lines.append(f"🎙️ 使用 faster-whisper（{fw_model}，本地免費）")
                    self.send(chat_id, "🎙️ 本地語音辨識中...")
                    try:
                        from faster_whisper import WhisperModel
                        logger.info(f"[Telegram] STT with faster-whisper ({fw_model}): {local_path}")
                        model = WhisperModel(fw_model, device="cpu", compute_type="int8")
                        segments, info = model.transcribe(local_path, language=None, beam_size=3)
                        text = " ".join(seg.text.strip() for seg in segments)
                        used_method = "faster-whisper"
                    except Exception as e:
                        logger.error(f"[Telegram] faster-whisper error: {e}")
                        self.send(chat_id, f"❌ faster-whisper 辨識失敗: {e}")
                        return
                elif stt_method == "openai-whisper":
                    api_key_env = stt_config.get("api_key_env", "OPENAI_API_KEY")
                    api_key = os.environ.get(api_key_env, "")
                    if api_key:
                        openai_model = stt_config.get("model", "whisper-1")
                        self.send(chat_id, "🎙️ OpenAI Whisper API 辨識中...")
                        try:
                            from openai import OpenAI
                            client = OpenAI(api_key=api_key)
                            with open(local_path, "rb") as f:
                                transcript = client.audio.transcriptions.create(
                                    model=openai_model, file=f
                                )
                            text = transcript.text or ""
                            used_method = "openai-whisper"
                        except Exception as e:
                            self.send(chat_id, f"❌ OpenAI Whisper API 失敗: {e}。嘗試其他方法...")
                    else:
                        status_lines.append(f"   ⚠️ {api_key_env} 未設定")
                elif stt_method == "google-speech":
                    status_lines.append("   ⚠️ Google Speech-to-Text 尚未實作")
                else:
                    status_lines.append(f"   ⚠️ 未知 STT method: {stt_method}")

            # Priority B2: Model-based STT (stt.model set but no stt.method)
            if not used_method:
                stt_model_id = stt_config.get("model", "").strip()
                if stt_model_id:
                    # Find the model in providers
                    stt_model_obj = next((m for m in all_models if m["id"] == stt_model_id), None)
                    if stt_model_obj and stt_model_obj.get("audio_input"):
                        # Use model's native audio input API
                        status_lines.append(f"🎙️ 使用模型原生音訊輸入：**{stt_model_id}**（audio_input=true）")
                        self.send(chat_id, f"🎙️ {stt_model_id} 音訊辨識中...")
                        try:
                            import base64 as _b64
                            provider = stt_model_obj["provider"]
                            # Find provider config for API credentials
                            pconf = config.get("providers", {}).get(provider, {})
                            api_key = os.environ.get(pconf.get("api_key_env", ""), "")
                            api_url = f"{pconf.get('base_url', '').rstrip('/')}/chat/completions"
                            with open(local_path, "rb") as f:
                                audio_b64 = _b64.b64encode(f.read()).decode()
                            data_url = f"data:audio/ogg;base64,{audio_b64}"
                            resp = httpx.post(
                                api_url,
                                json={
                                    "model": stt_model_id,
                                    "messages": [{
                                        "role": "user",
                                        "content": [
                                            {"type": "text", "text": "Transcribe this audio to text in Traditional Chinese (Cantonese). Return ONLY the transcription, no extra text."},
                                            {"type": "audio_url", "audio_url": {"url": data_url}},
                                        ],
                                    }],
                                    "max_tokens": 1024,
                                },
                                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                timeout=60,
                            )
                            data = resp.json()
                            text = (data.get("choices", [{}])[0]
                                    .get("message", {})
                                    .get("content", ""))
                            used_method = f"model-{stt_model_id}"
                        except Exception as e:
                            logger.error(f"[Telegram] Model STT error: {e}")
                            self.send(chat_id, f"❌ {stt_model_id} 音訊辨識失敗: {e}")
                    else:
                        status_lines.append(f"   ⚠️ stt.model={stt_model_id} 無 audio_input 能力")

            # Priority C: faster-whisper local fallback (only if no STT config at all)
            if not used_method and fw_available and not stt_config.get("model", "").strip():
                fw_model = stt_config.get("model", "base") if stt_method == "faster-whisper" else "base"
                status_lines.append(f"🎙️ 使用 faster-whisper（{fw_model}，本地免費）")
                self.send(chat_id, "🎙️ 本地語音辨識中...")
                try:
                    from faster_whisper import WhisperModel
                    logger.info(f"[Telegram] STT with faster-whisper ({fw_model}): {local_path}")
                    model = WhisperModel(fw_model, device="cpu", compute_type="int8")
                    segments, info = model.transcribe(local_path, language=None, beam_size=3)
                    text = " ".join(seg.text.strip() for seg in segments)
                    used_method = "faster-whisper"
                except Exception as e:
                    logger.error(f"[Telegram] faster-whisper error: {e}")
                    self.send(chat_id, f"❌ faster-whisper 辨識失敗: {e}")
                    return

            # ── Result: transcribe success or present options ──
            if used_method:
                if not text.strip():
                    self.send(chat_id, "🔇 聽唔到任何語音內容。")
                    return

                dur_str = "?"
                if used_method == "faster-whisper" and info and hasattr(info, 'duration') and isinstance(info.duration, (int, float)):
                    dur_str = f"{info.duration:.1f}s"
                self.send(chat_id, f"📝 辨識完成（{dur_str}）：\n\n{text[:200]}{'…' if len(text)>200 else ''}")

                prompt = (
                    f"[語音輸入 — 用戶語音訊息，已自動轉文字]\n"
                    f"用戶說：{text}\n\n"
                    f"請用繁體中文回應，分析並回答對方提問。"
                )
                self.send(chat_id, "🤔 BAW 分析中...")
                response = self._run_baw(prompt, chat_id=chat_id)
                self.send(chat_id, response)
                self._record_batch_result(chat_id, response[:200], "voice")
            else:
                # ── No STT method available — present diagnostics + options ──
                msg_parts = ["🎵 收到語音訊息，但目前 BAW 未有語音處理能力。\n"]

                # List all models with audio_input status
                msg_parts.append("**🔍 模型語音能力：**")
                for m in all_models:
                    ai = "✅" if m.get("audio_input") else "❌"
                    cur = " ← 當前主模型" if m["id"] == default_model_id else ""
                    msg_parts.append(f"  {ai} `{m['id']}`{cur}")
                if audio_models:
                    names = ", ".join(m["id"] for m in audio_models)
                    msg_parts.append(f"\n💡 有模型支援音訊輸入：`{names}`。用 `/model <name>` 切換。")
                msg_parts.append("")

                # Available STT options
                msg_parts.append("**⚙️ 可選 STT 方案：**")
                msg_parts.append(
                    "  1️⃣ **faster-whisper**（推薦，本地免費）\n"
                    "     ```\n"
                    "     pip install faster-whisper\n"
                    "     ```\n"
                    "     然後 ~/.baw/config.yaml 加入：\n"
                    "     ```yaml\n"
                    "     stt:\n"
                    "       method: \"faster-whisper\"\n"
                    "       model: \"base\"\n"
                    "     ```"
                )
                msg_parts.append(
                    "  2️⃣ **OpenAI Whisper API**（需 API key）\n"
                    "     在 ~/.baw/.env 加入：\n"
                    "     ```\n"
                    "     OPENAI_API_KEY=sk-...\n"
                    "     ```\n"
                    "     然後 ~/.baw/config.yaml 加入：\n"
                    "     ```yaml\n"
                    "     stt:\n"
                    "       method: \"openai-whisper\"\n"
                    "       api_key_env: \"OPENAI_API_KEY\"\n"
                    "       model: \"whisper-1\"\n"
                    "     ```"
                )
                msg_parts.append(
                    "  3️⃣ **Google Cloud Speech-to-Text**\n"
                    "     設定 Google Cloud 服務帳號 + config.yaml"
                )

                # If fw_available but not used (e.g. config issue), note it
                if fw_available and not used_method:
                    msg_parts.append(
                        "\n⚠️ faster-whisper 已安裝，但 stt.method 未設為 \"faster-whisper\"。"
                    )

                self.send(chat_id, "\n".join(msg_parts))

        except Exception as e:
            logger.error(f"[Telegram] Voice processing error: {e}")
            self.send(chat_id, f"❌ 語音處理錯誤: {e}")
        finally:
            self._release_slot()

    @staticmethod
    def _scan_all_models(config: dict) -> list[dict]:
        """Scan all providers/models from config and return flat list with capability info."""
        models = []
        providers = config.get("providers", {})
        for pname, pconf in providers.items():
            for m in pconf.get("models", []):
                models.append({
                    "id": m.get("id", ""),
                    "provider": pname,
                    "vision": m.get("vision", False),
                    "audio_input": m.get("audio_input", False),
                    "capabilities": m.get("capabilities", []),
                })
        return models

    def _send_as_tts(self, chat_id: str, text: str):
        """Convert response text to speech and send as audio message."""
        import os
        try:
            # Load BAW config to get TTS credentials
            baw = self._baw_ensure()
            config = baw["config"]

            # Find TTS capability
            from core.capabilities import resolve_capability
            tts_cap = resolve_capability(config, "tts")
            if tts_cap is None or tts_cap["type"] != "model":
                logger.warning("[TTS] No TTS-capable model configured")
                return

            # Get API key
            api_key = os.environ.get(tts_cap.get("api_key_env", ""), "")
            if not api_key:
                logger.warning(f"[TTS] No API key for {tts_cap.get('id', '?')}")
                return

            # Get TTS config
            tts_config = config.get("capabilities", {}).get("tts", {}).get("config", {})
            tts_model = tts_config.get("api_model", "speech-2.8-hd")
            voice = self._tts_voice or tts_config.get("voice", "male-tone-1")

            # Call TTS
            from core.tts import minimax_tts, save_audio_bytes
            audio_data = minimax_tts(
                text=text,
                api_key=api_key,
                voice=voice,
                model=tts_model,
                language="yue",
            )
            if audio_data is None:
                self.send(chat_id, "⚠️ TTS 生成失敗")
                return

            audio_path = save_audio_bytes(audio_data)
            self.send(chat_id, f"MEDIA:{audio_path}")

        except Exception as e:
            logger.error(f"[TTS] send error: {e}")

    def _poll_loop(self):
        """Long-polling loop."""
        if not self._client:
            logger.error("[Telegram] No client — can't poll")
            return

        while self._running:
            try:
                r = self._client.post(
                    f"{self._api_base}/getUpdates",
                    json={
                        "offset": self._offset,
                        "timeout": POLL_TIMEOUT,
                        "allowed_updates": ["message", "callback_query"],
                    },
                    timeout=POLL_TIMEOUT + 5,
                )
                if r.status_code != 200:
                    logger.warning(f"[Telegram] getUpdates HTTP {r.status_code}")
                    time.sleep(POLL_RETRY_DELAY)
                    continue

                data = r.json()
                if not data.get("ok"):
                    logger.warning(f"[Telegram] API error: {data}")
                    time.sleep(POLL_RETRY_DELAY)
                    continue

                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    self._handle_update(update)

            except httpx.TimeoutException:
                # Timeout is normal for long-poll — just retry
                continue
            except Exception as e:
                logger.error(f"[Telegram] Poll error: {e}")
                time.sleep(POLL_RETRY_DELAY)

    def _handle_update(self, update: dict):
        """Process a single Telegram update (non-blocking, concurrent)."""
        # ── Handle inline keyboard callback query ──
        cb = update.get("callback_query")
        if cb:
            self._handle_callback(cb)
            return

        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg["chat"]["id"])
        user_id = str(msg["from"]["id"])
        user_name = msg["from"].get("first_name", "User")
        text = msg.get("text", "").strip()

        # ── Handle reply-to: prepend quoted message context
        reply = msg.get("reply_to_message")
        if reply and text:
            reply_text = reply.get("text", "") or reply.get("caption", "") or ""
            if reply_text:
                reply_from = reply.get("from", {}).get("first_name", "User")
                text = f"> {reply_from}: {reply_text[:200]}\n\n{text}"

        if not text:
            # ── Non-text message — check media group FIRST ──
            media_group_id = msg.get("media_group_id")
            if media_group_id:
                self._buffer_media_group(chat_id, media_group_id, msg)
                return

            # ── Single non-text message (no group) — queue if busy, else process ──
            doc = msg.get("document")
            photo = msg.get("photo")
            video = msg.get("video")
            audio = msg.get("audio")
            voice = msg.get("voice")

            if doc:
                self._handle_media_msg(chat_id, user_id, user_name, msg, "document")
            elif photo:
                self._handle_media_msg(chat_id, user_id, user_name, msg, "photo")
            elif video:
                self.send(chat_id, "🎬 收到影片，但 BAW 暫時未支援影片處理。")
            elif audio or voice:
                self._handle_media_msg(chat_id, user_id, user_name, msg, "voice")
            # else: silent ignore for unknown types
            return

        # Access control
        if self._allowed and user_id not in self._allowed:
            self.send(chat_id, "⛔ You are not authorized to use this bot.")
            return

        logger.info(f"[Telegram] <{user_name}> {text[:80]}")

        # /stop — cancel running BAW immediately
        if text.strip().lower().startswith("/stop"):
            self._cancel_event.set()
            self.send(chat_id, f"⏹ Stopped {self._active_count} active task(s).")
            return

        # /tts — toggle text-to-speech
        if text.strip().lower().startswith("/tts"):
            args = text.strip().lower().split()
            if len(args) > 1:
                if args[1] in ("on", "true", "1", "yes"):
                    self._tts_enabled = True
                    self.send(chat_id, "🔊 TTS 已開啟 — 回覆會自動轉語音")
                elif args[1] in ("off", "false", "0", "no"):
                    self._tts_enabled = False
                    self.send(chat_id, "🔇 TTS 已關閉")
                else:
                    self._tts_voice = args[1]
                    self.send(chat_id, f"🎤 TTS 語音設為: {args[1]}")
            else:
                status = "開" if self._tts_enabled else "關"
                self.send(chat_id,
                          f"🔊 TTS 狀態: **{status}**\n"
                          f"   語音: `{self._tts_voice}`\n"
                          f"   用 `/tts on` 開啟\n"
                          f"   用 `/tts off` 關閉\n"
                          f"   用 `/tts <voice_id>` 切換語音")
            return

        # Debounce window: suppress new threads right after /stop
        if self._debounce_until and time.time() < self._debounce_until:
            self.send(chat_id, "⏳ Please wait a moment before sending a new request.")
            return

        # Acquire slot and start async processing in background thread
        if not self._acquire_slot():
            pos = self._enqueue_message(chat_id, user_id, user_name, text, msg)
            self.send(chat_id, f"⏳ Queued #{pos} — will process when a slot frees up ({self._active_count}/{self._max_concurrency} busy)")
            return

        self._cancel_event.clear()
        threading.Thread(
            target=self._process_message,
            args=(chat_id, user_id, user_name, text, msg),
            daemon=True,
        ).start()

    def _set_reaction(self, chat_id: int, message_id: int, emoji: str) -> bool:
        """Set a reaction emoji on a telegram message. Falls back to big emoji if unsupported."""
        try:
            resp = self._client.post(
                f"{self._api_base}/setMessageReaction",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                },
                timeout=5,
            )
            data = resp.json()
            if data.get("ok"):
                return True
            # Fallback: big reaction emoji
            fallback = {"🔄": "⚡", "🧠": "🤔", "✅": "👍", "❌": "👎"}
            fb = fallback.get(emoji)
            if fb:
                return self._set_reaction(chat_id, message_id, fb)
            return False
        except Exception:
            return False

    def _process_message(self, chat_id, user_id, user_name, text, msg):
        """Process a message in background thread (runs route() + send())."""
        logger.info(f"[Telegram] Processing: {text[:60]}...")

        # Get message_id for reactions
        _msg_id = msg.get("message_id") if isinstance(msg, dict) else None

        # Set initial reaction: 🔄 working/spinning
        if _msg_id:
            self._set_reaction(chat_id, _msg_id, "🔄")

        # Reaction update: after 2s switch to thinking
        _reaction_stop = threading.Event()
        if _msg_id:
            def _reaction_delayed():
                _reaction_stop.wait(2.0)
                if not _reaction_stop.is_set():
                    self._set_reaction(chat_id, _msg_id, "🧠")
            _rh = threading.Thread(target=_reaction_delayed, daemon=True)
            _rh.start()

        # Typing indicator heartbeat (respects cancel event)
        _typing_stop = threading.Event()
        def _typing_heartbeat():
            while not _typing_stop.is_set() and not self._cancel_event.is_set():
                try:
                    self._client.post(
                        f"{self._api_base}/sendChatAction",
                        json={"chat_id": chat_id, "action": "typing"},
                        timeout=5,
                    )
                except Exception:
                    pass
                _typing_stop.wait(3.0)
        _hb = threading.Thread(target=_typing_heartbeat, daemon=True)
        _hb.start()

        try:
            # Check cancelled before starting BAW
            if self._cancel_event.is_set():
                return

            msg_obj = Message(
                platform="telegram",
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                raw=msg,
            )
            response = self.route(msg_obj)
            _typing_stop.set()
            _reaction_stop.set()

            # Set reaction: ✅ success
            if _msg_id:
                self._set_reaction(chat_id, _msg_id, "✅")

            # If cancelled during processing, discard result
            if self._cancel_event.is_set():
                _reaction_stop.set()
                return
            if not response or not response.strip():
                logger.warning(f"[Telegram] Empty response from BAW for: {text[:80]}")
                response = "✅ Done. (No additional output.)"
            if response:
                self.send(chat_id, response)
                self._record_batch_result(chat_id, response[:200], "text")
                # TTS: convert response to speech if enabled
                if self._tts_enabled and response.strip():
                    self._send_as_tts(chat_id, response)

            # If restart was requested, exit cleanly so systemd restarts
            if self._restart_requested:
                import os
                logger.info("[Telegram] Restart requested — exiting")
                os._exit(0)
        except Exception as e:
            logger.error(f"[Telegram] Process error: {e}")
            _reaction_stop.set()
            # Set reaction: ❌ error
            if _msg_id:
                self._set_reaction(chat_id, _msg_id, "❌")
            if not self._cancel_event.is_set():
                try:
                    self.send(chat_id, f"❌ Error: {e}")
                except Exception:
                    pass
        finally:
            self._release_slot()

    # ── Inline keyboard for model selection (hierarchical: provider → model) ──

    def _get_providers_config(self) -> dict:
        """Get provider → models mapping from config + auto-discovered models."""
        try:
            baw = self._baw_ensure()
            config = baw["config"]
            providers = config.get("providers", {})
            result = {}
            for pname, pcfg in providers.items():

                models = [m["id"] for m in pcfg.get("models", [])]
                result[pname] = models
            # Try auto-discovery: fetch /v1/models for each provider
            discovered = self._discover_provider_models(providers)
            for pname, extra in discovered.items():
                existing = set(result.get(pname, []))
                new = [m for m in extra if m not in existing]
                if new:
                    result.setdefault(pname, []).extend(new)
            return result
        except Exception:
            # Fallback hardcoded
            return {
                "deepseek": ["deepseek-v4-flash"],
                "kimi": ["kimi-k2.6"],
                "minimax": ["MiniMax-M2.5"],
            }

    def _discover_provider_models(self, providers: dict) -> dict:
        """Try to auto-discover available models from each provider's API."""
        import os
        discovered = {}
        for pname, pcfg in providers.items():
            base_url = pcfg.get("base_url", "").rstrip("/")
            api_key = os.environ.get(pcfg.get("api_key_env", ""), "")
            if not base_url or not api_key:
                continue
            models_url = f"{base_url}/models"
            try:
                resp = httpx.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=5,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                # OpenAI-compatible format: {"data": [{"id": "..."}]}
                raw = data.get("data", [])
                if raw and isinstance(raw, list):
                    ids = [m.get("id", "") for m in raw if m.get("id")]
                    if ids:
                        discovered[pname] = ids
            except Exception:
                continue
        return discovered

    def _get_current_model_for_role(self, role: str) -> str | None:
        """Read current model for a role from config. Returns None if unset (inherits default)."""
        import yaml
        from pathlib import Path
        cfg_path = Path.home() / ".baw" / "config.yaml"
        if not cfg_path.exists():
            return None
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        model_cfg = cfg.get("model", {})
        adv_cfg = cfg.get("adversarial", {})
        exec_cfg = cfg.get("executor", {})
        key_map = {
            "default": model_cfg.get("default"),
            "angel": adv_cfg.get("angel_model"),
            "devil": adv_cfg.get("devil_model"),
            "executor": exec_cfg.get("model"),
        }
        return key_map.get(role)

    def _send_role_selector(self, chat_id: str, text: str) -> bool:
        """Show role selection keyboard with current settings."""
        import yaml
        from pathlib import Path
        cfg_path = Path.home() / ".baw" / "config.yaml"
        default_model = "unknown"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            default_model = cfg.get("model", {}).get("default", "unknown")

        role_order = [("default", "Default Model"), ("angel", "Angel Model"), ("devil", "Devil Model"), ("executor", "Executor Model")]
        rows = []
        status_lines = []
        for role_key, label in role_order:
            current = self._get_current_model_for_role(role_key)
            if current and role_key != "default":
                rows.append([{"text": f"  {label}: {current} ✎", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: `{current}`")
            elif current and role_key == "default":
                rows.append([{"text": f"  {label}: {current}", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: `{current}`")
            else:
                rows.append([{"text": f"  {label} = Default ({default_model})", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: = Default (`{default_model}`)")
        try:
            resp = self._client.post(
                f"{self._api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "**Model Configuration**\n" + "\n".join(status_lines),
                    "reply_markup": {"inline_keyboard": rows},
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
            data = resp.json()
            if data.get("ok"):
                self._selector_chat = chat_id
                self._selector_msg_id = data["result"]["message_id"]
            return data.get("ok", False)
        except Exception as e:
            logger.warning(f"[Telegram] Role selector error: {e}")
            return False

    def _send_role_buttons(self, chat_id: str, msg_id: int):
        """Edit existing message to show role selection buttons with current settings."""
        import yaml
        from pathlib import Path
        cfg_path = Path.home() / ".baw" / "config.yaml"
        default_model = "unknown"
        if cfg_path.exists():
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            default_model = cfg.get("model", {}).get("default", "unknown")

        role_order = [("default", "Default Model"), ("angel", "Angel Model"), ("devil", "Devil Model"), ("executor", "Executor Model")]
        rows = []
        status_lines = []
        for role_key, label in role_order:
            current = self._get_current_model_for_role(role_key)
            if current and role_key != "default":
                rows.append([{"text": f"  {label}: {current} ✎", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: `{current}`")
            elif current and role_key == "default":
                rows.append([{"text": f"  {label}: {current}", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: `{current}`")
            else:
                rows.append([{"text": f"  {label} = Default ({default_model})", "callback_data": f"role_select:{role_key}"}])
                status_lines.append(f"  {label}: = Default (`{default_model}`)")
        try:
            self._client.post(
                f"{self._api_base}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": "**Model Configuration**\n" + "\n".join(status_lines),
                    "reply_markup": {"inline_keyboard": rows},
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"[Telegram] Role buttons edit error: {e}")

    def _send_model_selector_text(self, chat_id: str, text: str) -> bool:
        """Parse [MODEL_SELECT] formatted text and send provider-level keyboard."""
        lines = text.strip().split("\n")
        title = lines[1] if len(lines) > 1 else "**Select Provider**"
        current_model = lines[2] if len(lines) > 2 else ""
        # Build provider keyboard
        providers = self._get_providers_config()
        rows = []
        for pname in providers:
            mark = "●" if any(current_model == m for m in providers[pname]) else " "
            rows.append([{"text": f"{mark} {pname}", "callback_data": f"provider_select:{pname}"}])
        if not rows:
            return self._send_text(chat_id, title)
        try:
            resp = self._client.post(
                f"{self._api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"{title}\nCurrent: `{current_model}`",
                    "reply_markup": {"inline_keyboard": rows},
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
            data = resp.json()
            if data.get("ok"):
                self._selector_chat = chat_id
                self._selector_msg_id = data["result"]["message_id"]
            return data.get("ok", False)
        except Exception as e:
            logger.warning(f"[Telegram] Provider selector send error: {e}")
            return False

    def _send_model_buttons(self, chat_id: str, msg_id: int, provider: str, current_model: str):
        """Edit message to show models for a specific provider."""
        providers = self._get_providers_config()
        models = providers.get(provider, [])
        if not models:
            return
        rows = []
        for m in models:
            label = f"\u25cf {m}" if m == current_model else f"  {m}"
            rows.append([{"text": label, "callback_data": f"model_select:{m}"}])
        # Back button
        rows.append([{"text": "\u2190 Back", "callback_data": "provider_select:__back__"}])
        try:
            self._client.post(
                f"{self._api_base}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": f"**{provider}** models:\nCurrent: `{current_model}`",
                    "reply_markup": {"inline_keyboard": rows},
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"[Telegram] Model buttons error: {e}")

    def _handle_callback(self, cb: dict):
        """Handle inline keyboard callback (role → provider → model selection)."""
        data = cb.get("data", "")
        msg = cb.get("message", {})
        chat_id = str(msg["chat"]["id"])
        cb_id = cb["id"]
        msg_id = msg.get("message_id")
        logger.info(f"[Telegram] Callback: {data} from {chat_id}")

        # ── Critical: acknowledge callback FIRST to stop Telegram loading spinner ──
        self._client.post(
            f"{self._api_base}/answerCallbackQuery",
            json={"callback_query_id": cb_id},
            timeout=5,
        )

        if data.startswith("role_select:"):
            role = data.split(":", 1)[1]
            if role == "__back__":
                # Go back to role selector (called from provider screen)
                self._send_role_buttons(chat_id, msg_id)
                return
            self._selector_role[chat_id] = role
            role_names = {"default": "Default", "angel": "Angel", "devil": "Devil", "executor": "Executor"}
            role_name = role_names.get(role, role)

            # Show provider keyboard for this role
            providers = self._get_providers_config()
            if msg_id:
                rows = []
                for pname in providers:
                    rows.append([{"text": pname, "callback_data": f"provider_select:{pname}"}])
                rows.append([{"text": "← Back", "callback_data": "role_select:__back__"}])
                try:
                    self._client.post(
                        f"{self._api_base}/editMessageText",
                        json={
                            "chat_id": chat_id,
                            "message_id": msg_id,
                            "text": f"**{role_name} Model**\nSelect provider:",
                            "reply_markup": {"inline_keyboard": rows},
                            "parse_mode": "Markdown",
                        },
                        timeout=5,
                    )
                except Exception as e:
                    logger.warning(f"[Telegram] Role→provider error: {e}")

        elif data.startswith("provider_select:"):
            pname = data.split(":", 1)[1]
            if pname == "__back__":
                # If we came from a role selector, go back there; else go to provider list
                role = self._selector_role.get(chat_id, "")
                if role and msg_id:
                    self._send_role_buttons(chat_id, msg_id)
                else:
                    try:
                        providers = self._get_providers_config()
                        title = "**Select Provider**"
                        cc = getattr(self, '_chat_config', {}).get(chat_id, {})
                        current = cc.get("model", "deepseek-v4-flash")
                        rows = []
                        for pn in providers:
                            rows.append([{"text": f"  {pn}", "callback_data": f"provider_select:{pn}"}])
                        self._client.post(
                            f"{self._api_base}/editMessageText",
                            json={
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": f"{title}\nCurrent: `{current}`",
                                "reply_markup": {"inline_keyboard": rows},
                                "parse_mode": "Markdown",
                            },
                            timeout=5,
                        )
                    except Exception as e:
                        logger.warning(f"[Telegram] Back button error: {e}")
            else:
                # Show models for this provider
                providers = self._get_providers_config()
                cc = getattr(self, '_chat_config', {}).get(chat_id, {})
                current = cc.get("model", "deepseek-v4-flash")
                self._send_model_buttons(chat_id, msg_id, pname, current)

        elif data.startswith("model_select:"):
            model_name = data.split(":", 1)[1]
            role = self._selector_role.get(chat_id, "default")

            # Map role to config key
            config_key_map = {
                "default": "model.default",
                "angel": "adversarial.angel_model",
                "devil": "adversarial.devil_model",
                "executor": "executor.model",
            }
            config_key = config_key_map.get(role, "model.default")
            role_names = {"default": "Default", "angel": "Angel", "devil": "Devil", "executor": "Executor"}
            role_name = role_names.get(role, role)

            route_msg = Message(
                platform="telegram",
                chat_id=chat_id,
                user_id=str(cb["from"]["id"]),
                text=f"/set {config_key} {model_name}",
            )
            result = self.route(route_msg)
            # Re-acknowledge with result text for feedback
            self._client.post(
                f"{self._api_base}/answerCallbackQuery",
                json={"callback_query_id": cb_id, "text": f"{role_name} → {model_name}"},
                timeout=5,
            )
            # Update button to show selection
            if msg_id:
                providers = self._get_providers_config()
                sel_provider = None
                for pn, mods in providers.items():
                    if model_name in mods:
                        sel_provider = pn
                        break
                if sel_provider:
                    self._send_model_buttons(chat_id, msg_id, sel_provider, model_name)
            if result:
                self.send(chat_id, result)

        else:
            self._client.post(
                f"{self._api_base}/answerCallbackQuery",
                json={"callback_query_id": cb_id, "text": "Unknown"},
                timeout=5,
            )
