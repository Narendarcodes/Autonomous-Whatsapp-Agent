"""Tests for argon2 password hashing (Task A2)."""
from app.core.security import hash_password, verify_password, needs_rehash


def test_hash_is_not_plaintext():
    h = hash_password("secret123")
    assert h != "secret123"
    assert h.startswith("$argon2")


def test_verify_correct_password():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True


def test_verify_wrong_password():
    h = hash_password("secret123")
    assert verify_password("wrong", h) is False


def test_verify_garbage_hash_returns_false():
    # Corrupted hash must return False, not raise
    assert verify_password("x", "not-a-hash") is False


def test_hashes_are_salted_unique():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b


def test_needs_rehash_current_policy_false():
    h = hash_password("pw")
    assert needs_rehash(h) is False
