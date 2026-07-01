"""WhatsApp service — sends messages via Evolution API.

Evolution API (atendai/evolution-api) is a Baileys-based WhatsApp REST
server. It replaces the original openwa/openwa image (which had no
public Docker image) while keeping the same overall architecture:
  - QR-code authenticated personal number session
  - Webhooks for inbound messages
  - REST API for outbound messages

Evolution API docs: https://doc.evolution-api.com/

Key differences from the original openwa REST contract:
  - Base URL: http://openwa:8080   (our compose maps port 2785 → 8080)
  - Auth header: apikey (not X-Api-Key)
  - Session concept: "instance" (created once, persists across restarts)
  - Send endpoint: POST /message/sendText/{instance}
  - Webhook events: { event: "messages.upsert", data: { ... } }
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis_client import cache_set

logger = get_logger(__name__)


def get_owner_country_code() -> str:
    owner_phone = "".join(ch for ch in settings.OWNER_WA_PHONE if ch.isdigit())
    if len(owner_phone) > 10:
        return owner_phone[:-10]
    return "91"


def validate_phone_number(phone: str) -> dict:
    """Full phone number validation using Google libphonenumber.

    Returns a dict with:
      - is_valid (bool)
      - digits (str): digits-only E.164 without + on success
      - country_code (str): e.g. "91" for India, "1" for USA
      - error (str | None): human-readable error on failure
    """
    if not phone:
        return {"is_valid": False, "digits": "", "country_code": "", "error": "Phone number is empty."}
    
    # Handle group JIDs separately — they are not phone numbers
    if "@g.us" in phone or (phone.strip().replace("-", "").replace("@", "").replace(".", "").isdigit() and "-" in phone) or (phone.strip().isdigit() and len(phone.strip()) == 18 and phone.strip().startswith("1203")):
        digits = "".join(c for c in phone.split("@")[0] if c.isdigit() or c == "-")
        return {"is_valid": True, "digits": digits, "country_code": "", "error": None, "is_group": True}

    try:
        import phonenumbers
        from phonenumbers import NumberParseException

        # Strip JID suffix and non-digit prefix noise
        raw = phone.split("@")[0].split(":")[0].strip()
        
        # Determine default region for 10-digit local numbers (fall back to owner's country)
        country_code = get_owner_country_code()
        # Map country code digits to ISO alpha-2 for phonenumbers.parse()
        CC_TO_REGION = {
            "1": "US", "7": "RU", "20": "EG", "27": "ZA", "30": "GR", "31": "NL",
            "32": "BE", "33": "FR", "34": "ES", "36": "HU", "39": "IT", "40": "RO",
            "41": "CH", "43": "AT", "44": "GB", "45": "DK", "46": "SE", "47": "NO",
            "48": "PL", "49": "DE", "51": "PE", "52": "MX", "54": "AR", "55": "BR",
            "56": "CL", "57": "CO", "60": "MY", "61": "AU", "62": "ID", "63": "PH",
            "64": "NZ", "65": "SG", "66": "TH", "81": "JP", "82": "KR", "84": "VN",
            "86": "CN", "90": "TR", "91": "IN", "92": "PK", "93": "AF", "94": "LK",
            "95": "MM", "98": "IR", "212": "MA", "213": "DZ", "216": "TN", "218": "LY",
            "220": "GM", "221": "SN", "234": "NG", "254": "KE", "255": "TZ", "256": "UG",
            "971": "AE", "972": "IL", "973": "BH", "974": "QA", "966": "SA", "960": "MV",
            "880": "BD", "886": "TW", "852": "HK", "853": "MO",
        }
        default_region = CC_TO_REGION.get(country_code, "IN")
        
        # If the number has no + prefix, try to intelligently parse it
        cleaned = "".join(c for c in raw if c.isdigit() or c == "+")
        if not cleaned.startswith("+"):
            if len("".join(c for c in cleaned if c.isdigit())) == 10:
                cleaned = f"+{country_code}{cleaned.lstrip('+')}"
            else:
                # Try prepending '+' to see if it parses as a valid international number
                try_intl = f"+{cleaned}"
                try:
                    parsed_intl = phonenumbers.parse(try_intl, default_region)
                    if phonenumbers.is_valid_number(parsed_intl):
                        cleaned = try_intl
                except Exception:
                    pass
        
        parsed = phonenumbers.parse(cleaned, default_region)
        
        if not phonenumbers.is_valid_number(parsed):
            return {
                "is_valid": False, "digits": "", "country_code": "",
                "error": "Invalid phone number. Please check the country code and number."
            }
        
        national_cc = str(parsed.country_code)
        national_number = str(parsed.national_number)
        digits = national_cc + national_number
        
        return {"is_valid": True, "digits": digits, "country_code": national_cc, "error": None}
    
    except Exception as e:
        # Fallback: basic digit-length validation if phonenumbers library unavailable
        logger.warning("phonenumbers validation error: %s — falling back to basic check", e)
        raw_digits = "".join(c for c in phone.split("@")[0] if c.isdigit())
        if len(raw_digits) == 10:
            raw_digits = get_owner_country_code() + raw_digits
        if 7 <= len(raw_digits) <= 15:
            return {"is_valid": True, "digits": raw_digits, "country_code": raw_digits[:-10] if len(raw_digits) > 10 else "", "error": None}
        return {"is_valid": False, "digits": "", "country_code": "", "error": "Invalid phone number length."}


def normalize_phone_number(phone: str) -> str:
    """Normalize phone number to E.164 digits string (no +). Returns empty string on invalid."""
    result = validate_phone_number(phone)
    return result["digits"] if result["is_valid"] else ""


# The Evolution API instance name — created once during setup, reused forever.
INSTANCE_NAME = settings.OPENWA_SESSION_ID  # "my-session"
WEBHOOK_EVENTS = ["MESSAGES_UPSERT", "CONNECTION_UPDATE", "QRCODE_UPDATED", "SEND_MESSAGE"]


def _qr_webhook_url() -> str:
    """Use a dedicated endpoint for QR events so setup can cache the image."""
    if settings.OPENWA_WEBHOOK_URL.endswith("/webhook/openwa"):
        return settings.OPENWA_WEBHOOK_URL.removesuffix("/webhook/openwa") + "/webhook/qr"
    return settings.OPENWA_WEBHOOK_URL.rstrip("/") + "/qr"


def _extract_qr_data(value) -> str:
    if isinstance(value, str):
        return value if ("base64" in value or len(value) > 100) else ""
    if isinstance(value, dict):
        for key in ("base64", "qrcode", "qr", "code"):
            found = _extract_qr_data(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _extract_qr_data(child)
            if found:
                return found
    return ""


class WhatsAppService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict:
        return {"apikey": settings.OPENWA_API_KEY, "Content-Type": "application/json"}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.OPENWA_BASE_URL.rstrip("/"),
                timeout=httpx.Timeout(
                    connect=settings.HTTP_CONNECT_TIMEOUT,
                    read=settings.HTTP_READ_TIMEOUT,
                    write=settings.HTTP_WRITE_TIMEOUT,
                    pool=settings.HTTP_POOL_TIMEOUT,
                ),
                headers=self._headers(),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def to_chat_id(target: str) -> str:
        """Normalise a phone or JID to Evolution API format.

        Evolution API uses the same WhatsApp JID format:
          personal: "919999999999@s.whatsapp.net"
          group:    "123456789-123456@g.us"
        """
        target = (target or "").strip()
        if target.endswith("@g.us"):
            return target
        if target.endswith("@s.whatsapp.net"):
            return target
        if "-" in target or len(target) == 18 or (len(target) > 15 and target.startswith("1203")):
            cleaned = "".join(ch for ch in target if ch.isdigit() or ch == "-")
            return f"{cleaned}@g.us"
        cleaned = "".join(ch for ch in target if ch.isdigit())
        return f"{cleaned}@s.whatsapp.net" if cleaned else ""

    async def send_text(self, to: str, message: str, force_primary: bool = False) -> bool:
        """Send a plain-text message to a phone or group JID."""
        number = self.to_chat_id(to)
        if not number:
            logger.warning("send_text called with empty target")
            return False

        # Route through agent instance if dual_number mode is active and agent is connected
        use_agent = False
        if not force_primary:
            to_clean = normalize_phone_number(to)
            owner_phone_clean = normalize_phone_number(settings.OWNER_WA_PHONE)
            if to_clean and owner_phone_clean and to_clean == owner_phone_clean:
                try:
                    from app.services.preferences_service import preferences_service
                    from app.models.models import User
                    from app.db.database import AsyncSessionLocal
                    from sqlalchemy import select

                    async with AsyncSessionLocal() as db:
                        result = await db.execute(select(User).where(User.is_owner == True))
                        owner = result.scalar_one_or_none()
                        if owner:
                            bot_mode = await preferences_service.get(owner.id, "bot_mode")
                            bot_phone = await preferences_service.get(owner.id, "bot_phone")
                            if bot_mode == "dual_number" and bot_phone:
                                from app.services.agent_instance_service import agent_instance_service
                                status = await agent_instance_service.get_agent_instance_status()
                                if status.get("state") == "open":
                                    use_agent = True
                except Exception as e:
                    logger.error("Failed to check agent routing state in send_text: %s", e)

        if use_agent:
            from app.services.agent_instance_service import agent_instance_service
            return await agent_instance_service.send_via_agent(to, message)

        url = f"/message/sendText/{INSTANCE_NAME}"
        payload = {"number": number, "text": message}

        client = await self._get_client()
        for attempt in range(settings.RETRY_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Sent message to %s", number)
                    # Cache the sent message ID to avoid infinite self-reply loops
                    try:
                        data = resp.json()
                        sent_msg_id = data.get("key", {}).get("id")
                        if sent_msg_id:
                            await cache_set(f"sent_message:{sent_msg_id}", "1", ttl_seconds=3600)
                            logger.debug("Cached sent message ID: %s", sent_msg_id)
                    except Exception as e:
                        logger.error("Failed to parse/cache sent message ID: %s", e)
                    return True
                logger.warning(
                    "Evolution API send_text %s → %s: %s",
                    number, resp.status_code, resp.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.warning("send_text attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(
                min(settings.RETRY_BASE_DELAY * (2 ** attempt), settings.RETRY_MAX_DELAY)
            )
        return False

    async def instance_status(self) -> dict | None:
        """Return the current instance connection state."""
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/connectionState/{INSTANCE_NAME}")
            if resp.status_code == 200:
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("instance_status error: %s", exc)
        return None

    def _instance_payload(self) -> dict:
        return {
            "instanceName": INSTANCE_NAME,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
            "webhook": {
                "url": settings.OPENWA_WEBHOOK_URL,
                "byEvents": False,
                "base64": True,
                "events": WEBHOOK_EVENTS,
            },
            "qrcodeWebhook": {"url": _qr_webhook_url()},
        }

    async def configure_webhook(self) -> bool:
        """Force the existing Evolution instance to use the current webhook config."""
        client = await self._get_client()
        payload = {
            "webhook": {
                "enabled": True,
                "url": settings.OPENWA_WEBHOOK_URL,
                "byEvents": False,
                "base64": True,
                "events": WEBHOOK_EVENTS,
            }
        }
        try:
            resp = await client.post(f"/webhook/set/{INSTANCE_NAME}", json=payload)
            if resp.status_code in (200, 201):
                logger.info("Evolution API webhook configured for %s", INSTANCE_NAME)
                return True
            logger.warning("Webhook configure failed %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            logger.error("configure_webhook error: %s", exc)
        return False

    async def create_instance(self) -> bool:
        """Create the WhatsApp instance (only needed once on first boot)."""
        client = await self._get_client()
        payload = self._instance_payload()
        try:
            resp = await client.post("/instance/create", json=payload)
            if resp.status_code in (200, 201):
                qr_data = _extract_qr_data(resp.json())
                if qr_data:
                    await cache_set("whatsapp:qr_code", qr_data, ttl_seconds=180)
                    logger.info("Evolution API QR code cached from create response")
                logger.info("Evolution API instance created: %s", INSTANCE_NAME)
                await self.configure_webhook()
                return True
            if resp.status_code == 403:
                logger.info("Instance already exists: %s", INSTANCE_NAME)
                return await self.configure_webhook()
            logger.error(
                "Instance create failed %s: %s", resp.status_code, resp.text[:200]
            )
        except httpx.HTTPError as exc:
            logger.error("create_instance error: %s", exc)
        return False

    async def delete_instance(self) -> bool:
        """Delete the WhatsApp instance (disconnect session)."""
        client = await self._get_client()
        try:
            resp = await client.delete(f"/instance/delete/{INSTANCE_NAME}")
            if resp.status_code in (200, 201):
                from app.db.redis_client import cache_set
                await cache_set("whatsapp:qr_code", "", ttl_seconds=1)
                await cache_set("whatsapp:connection_state", "disconnected", ttl_seconds=1)
                logger.info("Evolution API instance deleted: %s", INSTANCE_NAME)
                return True
            logger.error("Instance delete failed %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("delete_instance error: %s", exc)
        return False


    async def get_qr_code(self) -> bytes | None:
        """Return the raw QR code image bytes for the setup flow."""
        client = await self._get_client()
        try:
            resp = await client.get(f"/instance/qrcode/{INSTANCE_NAME}")
            if resp.status_code == 200:
                data = resp.json()
                # Evolution returns QR as base64 or as a raw endpoint
                qr_url = data.get("qrcode") or data.get("qr")
                if qr_url and qr_url.startswith("http"):
                    img_resp = await client.get(qr_url)
                    return img_resp.content
        except httpx.HTTPError as exc:
            logger.error("get_qr_code error: %s", exc)
        return None

    async def get_bot_phone(self) -> str | None:
        """Fetch the bot's own JID/phone from Evolution API fetchInstances."""
        client = await self._get_client()
        try:
            resp = await client.get("/instance/fetchInstances")
            if resp.status_code == 200:
                instances = resp.json()
                for inst in instances:
                    if inst.get("name") == INSTANCE_NAME:
                        owner_jid = inst.get("ownerJid")
                        if owner_jid:
                            return owner_jid.split("@")[0].split(":")[0].lstrip("+")
        except Exception as exc:
            logger.error("get_bot_phone error: %s", exc)
        return None

    async def download_media(self, message_id: str) -> str | None:
        """Download a media file (like audio) from Evolution API by its message ID.
        
        Returns the raw base64 data string, or None if failed.
        """
        url = f"/chat/getBase64FromMediaMessage/{INSTANCE_NAME}"
        payload = {
            "message": {
                "key": {
                    "id": message_id
                }
            },
            "convertToMp4": False
        }
        client = await self._get_client()
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 201):
                data = resp.json()
                base64_str = data.get("base64") or data.get("response", {}).get("base64")
                if base64_str:
                    return base64_str
                logger.warning("No base64 data found in download_media response: %s", resp.text[:200])
            else:
                logger.warning("download_media failed %s: %s", resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.error("download_media error: %s", exc)
        return None

    async def send_audio(self, to: str, audio_base64: str) -> bool:
        """Send an audio message (speech output) to a JID."""
        number = self.to_chat_id(to)
        if not number:
            logger.warning("send_audio called with empty target")
            return False

        url = f"/message/sendWhatsAppAudio/{INSTANCE_NAME}"
        if not audio_base64.startswith("data:"):
            audio_base64 = f"data:audio/ogg;base64,{audio_base64}"

        payload = {
            "number": number,
            "audio": audio_base64,
            "delay": 1000
        }

        client = await self._get_client()
        for attempt in range(settings.RETRY_MAX_ATTEMPTS):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code in (200, 201):
                    logger.info("Sent audio message to %s", number)
                    try:
                        data = resp.json()
                        sent_msg_id = data.get("key", {}).get("id")
                        if sent_msg_id:
                            await cache_set(f"sent_message:{sent_msg_id}", "1", ttl_seconds=3600)
                            logger.debug("Cached sent audio message ID: %s", sent_msg_id)
                    except Exception as e:
                        logger.error("Failed to parse/cache sent audio message ID: %s", e)
                    return True
                
                if resp.status_code == 404:
                    fallback_url = f"/message/sendAudio/{INSTANCE_NAME}"
                    resp_fallback = await client.post(fallback_url, json=payload)
                    if resp_fallback.status_code in (200, 201):
                        logger.info("Sent audio message to %s (via sendAudio)", number)
                        try:
                            data = resp_fallback.json()
                            sent_msg_id = data.get("key", {}).get("id")
                            if sent_msg_id:
                                await cache_set(f"sent_message:{sent_msg_id}", "1", ttl_seconds=3600)
                        except Exception:
                            pass
                        return True
                    logger.warning("Evolution API send_audio fallback failed %s: %s", resp_fallback.status_code, resp_fallback.text[:200])

                logger.warning(
                    "Evolution API send_audio %s → %s: %s",
                    number, resp.status_code, resp.text[:200],
                )
            except httpx.HTTPError as exc:
                logger.warning("send_audio attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(
                min(settings.RETRY_BASE_DELAY * (2 ** attempt), settings.RETRY_MAX_DELAY)
            )
        return False

    async def get_profile_picture(self, number: str) -> str | None:
        """Fetch profile picture URL of a phone number from Evolution API."""
        chat_id = self.to_chat_id(number)
        if not chat_id:
            return None
        client = await self._get_client()
        try:
            resp = await client.post(
                f"/chat/fetchProfilePictureUrl/{INSTANCE_NAME}",
                json={"number": chat_id}
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("profilePictureUrl") or data.get("url")
        except Exception as exc:
            logger.error("get_profile_picture error: %s", exc)
        return None

    async def get_contact_info(self, number: str) -> dict | None:
        """Fetch contact details from Evolution API (e.g. display name)."""
        chat_id = self.to_chat_id(number)
        if not chat_id:
            return None
        client = await self._get_client()
        try:
            payload = {"where": {"remoteJid": chat_id}}
            resp = await client.post(
                f"/chat/findContacts/{INSTANCE_NAME}",
                json=payload
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    for item in data:
                        item_jid = item.get("remoteJid") or item.get("id") or ""
                        if item_jid == chat_id:
                            return item
                    return data[0]
        except Exception as exc:
            logger.error("get_contact_info error: %s", exc)
        return None

    async def sync_contacts(self) -> list[dict]:
        """Fetch and cache all contacts and groups from Evolution API."""
        client = await self._get_client()
        filtered_contacts = []
        
        # 1. Fetch personal contacts
        try:
            resp = await client.post(
                f"/chat/findContacts/{INSTANCE_NAME}",
                json={}
            )
            if resp.status_code == 200:
                contacts = resp.json()
                if isinstance(contacts, list):
                    for c in contacts:
                        jid = c.get("remoteJid") or ""
                        if not jid:
                            continue
                        if "@lid" in jid:
                            continue
                        is_group = jid.endswith("@g.us")
                        if is_group:
                            continue
                        phone = jid.split("@")[0].split(":")[0].lstrip("+")
                        if not phone.isdigit():
                            continue
                        name = c.get("name")
                        push_name = c.get("pushName") or c.get("pushname")
                        if not name and push_name:
                            name = push_name
                        if name:
                            name = name.lstrip("~").strip()
                        filtered_contacts.append({
                            "phone": phone,
                            "name": name or f"User {phone[-4:]}",
                            "jid": jid,
                            "is_group": False
                        })
        except Exception as exc:
            logger.error("sync_contacts (personal) error: %s", exc)

        # 2. Fetch groups
        try:
            resp_groups = await client.post(
                f"/chat/findChats/{INSTANCE_NAME}",
                json={}
            )
            if resp_groups.status_code == 200:
                groups = resp_groups.json()
                if isinstance(groups, list):
                    for g in groups:
                        jid = g.get("remoteJid") or g.get("id") or ""
                        if not jid or not jid.endswith("@g.us"):
                            continue
                        phone = jid.split("@")[0].split(":")[0].lstrip("+")
                        name = g.get("pushName") or g.get("subject") or g.get("name") or f"Group {phone[-4:]}"
                        if name:
                            name = name.strip()
                        # Avoid duplicates
                        if not any(c["jid"] == jid for c in filtered_contacts):
                            filtered_contacts.append({
                                "phone": phone,
                                "name": name,
                                "jid": jid,
                                "is_group": True
                            })
            else:
                logger.warning("findChats failed with status %d: %s", resp_groups.status_code, resp_groups.text[:200])
        except Exception as exc:
            logger.error("sync_contacts (groups) error: %s", exc)

        # Cache results
        try:
            if filtered_contacts:
                import json
                await cache_set("whatsapp:contacts_cache", json.dumps(filtered_contacts), ttl_seconds=86400)
                logger.info("Synced and cached %d WhatsApp contacts & groups", len(filtered_contacts))
        except Exception as exc:
            logger.error("sync_contacts cache_set error: %s", exc)

        return filtered_contacts


whatsapp_service = WhatsAppService()
