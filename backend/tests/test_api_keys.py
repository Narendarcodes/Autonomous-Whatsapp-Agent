import os
import yaml
import pytest
from unittest.mock import patch
from app.models.models import ApiKey
from app.core.security import encrypt_token, decrypt_token
from app.services.litellm_service import rebuild_litellm_config, TEMPLATE_PATH, TARGET_PATH

@pytest.mark.asyncio
async def test_api_key_encryption_and_crud(db_session):
    """Test creating, encrypting, reading, and decrypting API keys in the DB."""
    # 1. Create a key
    raw_key = "gsk_test_api_key_12345_groq"
    encrypted = encrypt_token(raw_key)
    
    new_key = ApiKey(
        name="Groq Test Key",
        provider="groq",
        api_key_enc=encrypted,
        is_active=True
    )
    db_session.add(new_key)
    await db_session.commit()
    await db_session.refresh(new_key)
    
    assert new_key.id is not None
    assert new_key.name == "Groq Test Key"
    assert new_key.provider == "groq"
    assert new_key.api_key_enc != raw_key
    
    # 2. Decrypt key
    decrypted = decrypt_token(new_key.google_access_token_enc if hasattr(new_key, 'google_access_token_enc') else new_key.api_key_enc)
    assert decrypted == raw_key
    
    # 3. Clean up
    await db_session.delete(new_key)
    await db_session.commit()


@pytest.mark.asyncio
async def test_rebuild_litellm_config(db_session):
    """Test that rebuild_litellm_config correctly overrides templates and handles duplicates."""
    # Seed 2 active Groq keys and 1 active OpenRouter key
    key1 = ApiKey(
        name="Groq Key A",
        provider="groq",
        api_key_enc=encrypt_token("gsk_key_A"),
        is_active=True
    )
    key2 = ApiKey(
        name="Groq Key B",
        provider="groq",
        api_key_enc=encrypt_token("gsk_key_B"),
        is_active=True
    )
    key3 = ApiKey(
        name="OpenRouter Key",
        provider="openrouter",
        api_key_enc=encrypt_token("sk_or_key"),
        is_active=True
    )
    db_session.add_all([key1, key2, key3])
    await db_session.commit()
    
    # Run config rebuilder against a mock file location or write directly
    import tempfile
    mock_target_path = os.path.join(tempfile.gettempdir(), "mock_litellm_config.yaml")
    
    with patch("app.services.litellm_service.TARGET_PATH", mock_target_path):
        success = await rebuild_litellm_config(db_session)
        assert success is True
        assert os.path.exists(mock_target_path)
        
        # Read the mock config
        with open(mock_target_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            
        # Verify model list contains duplicated keys for Groq and single key for OpenRouter
        models = config.get("model_list", [])
        assert len(models) > 0
        
        groq_count = 0
        or_count = 0
        gemini_count = 0
        
        groq_keys = []
        or_keys = []
        
        for m in models:
            model_id = m["litellm_params"]["model"]
            key_val = m["litellm_params"]["api_key"]
            
            if model_id.startswith("groq/"):
                groq_count += 1
                groq_keys.append(key_val)
            elif model_id.startswith("openrouter/"):
                or_count += 1
                or_keys.append(key_val)
            elif model_id.startswith("gemini/"):
                gemini_count += 1
                # Should remain env var default
                assert key_val == "os.environ/GOOGLE_AI_API_KEY"
                
        # Since template has 2 Groq models: llama-3.3-70b (order 1) and llama-3.1-8b (order 4)
        # And we seeded 2 Groq keys, each Groq model should be duplicated: 2 * 2 = 4 Groq models.
        assert groq_count == 4
        assert "gsk_key_A" in groq_keys
        assert "gsk_key_B" in groq_keys
        
        # Since template has 2 OpenRouter models: gemma-4-31b (order 2) and gemma-4-26b (order 3)
        # And we seeded 1 OpenRouter key, each should stay single: 2 * 1 = 2 OpenRouter models.
        assert or_count == 2
        assert "sk_or_key" in or_keys
        
        # Since we seeded no Gemini keys, the single template Gemini model stays as default
        assert gemini_count == 1
        
    # Clean up mock file and database records
    if os.path.exists(mock_target_path):
        os.remove(mock_target_path)
        
    await db_session.delete(key1)
    await db_session.delete(key2)
    await db_session.delete(key3)
    await db_session.commit()
