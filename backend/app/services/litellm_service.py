import os
import yaml
import logging
import copy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import ApiKey
from app.core.security import decrypt_token

logger = logging.getLogger(__name__)

# Template lives inside app/ since app/ is mounted as volume in development
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "litellm_config.yaml.template")
TARGET_PATH = "/app/litellm_config.yaml"

async def rebuild_litellm_config(db: AsyncSession) -> bool:
    """
    Rebuild the LiteLLM config file by reading the template, querying all active
    API keys in the database, decrypting them, and writing them into the model configurations.
    If multiple active keys exist for the same provider, duplicate model entries will be
    created to enable LiteLLM fallback routing across multiple accounts.
    """
    try:
        if not os.path.exists(TEMPLATE_PATH):
            logger.error(f"LiteLLM config template not found at {TEMPLATE_PATH}")
            return False

        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Get active api keys
        result = await db.execute(select(ApiKey).where(ApiKey.is_active == True))
        api_keys = result.scalars().all()

        # Group keys by provider
        keys_by_provider = {}
        for key in api_keys:
            try:
                decrypted = decrypt_token(key.api_key_enc)
                keys_by_provider.setdefault(key.provider.lower(), []).append(decrypted)
            except Exception as dec_err:
                logger.error(f"Failed to decrypt key '{key.name}' (id={key.id}): {dec_err}")

        # Rebuild model list
        new_model_list = []
        original_model_list = config.get("model_list", [])

        for model_entry in original_model_list:
            model_id = model_entry.get("litellm_params", {}).get("model", "")
            
            # Determine provider
            provider = None
            if model_id.startswith("groq/"):
                provider = "groq"
            elif model_id.startswith("openrouter/"):
                provider = "openrouter"
            elif model_id.startswith("gemini/"):
                provider = "gemini"
            
            # If we have keys for this provider, duplicate model configurations
            if provider and provider in keys_by_provider and keys_by_provider[provider]:
                provider_keys = keys_by_provider[provider]
                for idx, key_val in enumerate(provider_keys):
                    new_entry = copy.deepcopy(model_entry)
                    new_entry["litellm_params"]["api_key"] = key_val
                    # Differentiate the model info ID if present
                    if "model_info" in new_entry and "id" in new_entry["model_info"]:
                        new_entry["model_info"]["id"] = f"{new_entry['model_info']['id']}-key-{idx+1}"
                    new_model_list.append(new_entry)
            else:
                # Keep default (with env variable reference)
                new_model_list.append(model_entry)

        config["model_list"] = new_model_list

        # Write rebuilt configuration to the target path
        with open(TARGET_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info(f"Successfully rebuilt LiteLLM configuration at {TARGET_PATH}")
        return True

    except Exception as e:
        logger.error(f"Error rebuilding LiteLLM config: {e}")
        return False
