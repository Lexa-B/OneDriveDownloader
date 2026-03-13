import base64

import pytest

from src.quickxor import QuickXorHash

# Reference values verified against the Microsoft C# reference implementation:
# https://learn.microsoft.com/en-us/onedrive/developer/code-snippets/quickxorhash
REFERENCE_VECTORS = [
    (b"", "AAAAAAAAAAAAAAAAAAAAAAAAAAA="),
    (b"\x01", "AQAAAAAAAAAAAAAAAQAAAAAAAAA="),
    (b"hello", "aCgDG9jwBgAAAAAABQAAAAAAAAA="),
    (b"The quick brown fox jumps over the lazy dog", "bMSlbysmxJL6S75XwfMcQZOpcr4="),
    (bytes(range(256)), "QkGEfSisZcA7k+FCh71r2dbCayY="),
    (b"A" * 160, "AAAAAAAAAAAAAAAAoAAAAAAAAAA="),
    (b"test data for hashing", "VSSwXKsa3kKCgQ5hFYEZ3iAHEKA="),
    (bytes(range(256)) * 100, "AAAAAAAAAAAAAAAAAGQAAAAAAAA="),
]


@pytest.mark.parametrize("data,expected", REFERENCE_VECTORS, ids=[
    "empty", "single_byte", "hello", "pangram_43b", "bytes_0_255",
    "160_As", "test_data", "large_25600b",
])
def test_reference_vectors(data, expected):
    """Hash output matches the Microsoft C# reference implementation."""
    h = QuickXorHash()
    h.update(data)
    assert h.base64_digest() == expected


def test_empty_input():
    """Empty input produces a 20-byte hash of all zeros."""
    h = QuickXorHash()
    digest = h.digest()
    assert len(digest) == 20
    assert digest == b"\x00" * 20


def test_single_byte():
    """Hashing a single byte produces a known result."""
    h = QuickXorHash()
    h.update(b"\x01")
    result = h.base64_digest()
    assert result == "AQAAAAAAAAAAAAAAAQAAAAAAAAA="


def test_incremental_equals_single():
    """Feeding data in chunks produces same result as all at once."""
    data = b"The quick brown fox jumps over the lazy dog"

    h1 = QuickXorHash()
    h1.update(data)

    h2 = QuickXorHash()
    h2.update(data[:10])
    h2.update(data[10:25])
    h2.update(data[25:])

    assert h1.base64_digest() == h2.base64_digest()


def test_different_inputs_different_hashes():
    """Different inputs produce different hashes."""
    h1 = QuickXorHash()
    h1.update(b"hello")

    h2 = QuickXorHash()
    h2.update(b"world")

    assert h1.base64_digest() != h2.base64_digest()


def test_output_is_base64():
    """base64_digest returns valid base64 string."""
    h = QuickXorHash()
    h.update(b"test data for hashing")
    result = h.base64_digest()
    # Should round-trip through base64
    decoded = base64.b64decode(result)
    assert len(decoded) == 20
    assert base64.b64encode(decoded).decode() == result


def test_large_data():
    """Hash works correctly on data larger than the 160-bit block."""
    h = QuickXorHash()
    data = bytes(range(256)) * 100  # 25,600 bytes
    h.update(data)
    result = h.base64_digest()
    assert isinstance(result, str)

    # Verify consistency
    h2 = QuickXorHash()
    h2.update(data)
    assert h2.base64_digest() == result
