"""Phone number validation and normalisation utilities.

Shared by the setup API, ACL matching and owner resolution. Independent of
any transport (Hermes bridge or otherwise).
"""
from app.core.config import settings
from app.core.logging import get_logger

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
