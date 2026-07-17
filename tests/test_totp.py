"""TOTP pure-logic tests (ROLO-0006).

The code-generation cases are the canonical RFC 6238 Appendix B known-answer vectors,
verified against all three hash algorithms. The detection/parsing cases lock in the field
trigger rule: an otpauth:// URI always qualifies; a bare base32 seed qualifies only when the
field label hints 2FA. All GTK-free, so they run anywhere pytest does.
"""

import base64

import rolodex


# RFC 6238 Appendix B shared secrets, ASCII (the raw HMAC keys, pre-base32).
SEED_SHA1 = b"12345678901234567890"
SEED_SHA256 = b"12345678901234567890123456789012"
SEED_SHA512 = b"1234567890123456789012345678901234567890123456789012345678901234"

# (unix time, code_sha1, code_sha256, code_sha512) — all 8-digit, period 30.
RFC6238_VECTORS = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


def test_totp_code_matches_rfc6238_vectors():
    for ts, c1, c256, c512 in RFC6238_VECTORS:
        assert rolodex.totp_code(SEED_SHA1, ts, digits=8, period=30, algorithm="sha1") == c1
        assert rolodex.totp_code(SEED_SHA256, ts, digits=8, period=30, algorithm="sha256") == c256
        assert rolodex.totp_code(SEED_SHA512, ts, digits=8, period=30, algorithm="sha512") == c512


def test_totp_code_defaults_to_6_digits_sha1_period30():
    # Google Authenticator's ubiquitous defaults; 6-digit truncation of the SHA1 vector at t=59.
    assert rolodex.totp_code(SEED_SHA1, 59) == "287082"


def test_totp_code_is_zero_padded():
    # Some counters truncate to a value with leading zeros; the string must keep its width.
    code = rolodex.totp_code(SEED_SHA1, 1111111109, digits=8, period=30, algorithm="sha1")
    assert code == "07081804"
    assert len(code) == 8


def test_totp_remaining_counts_down_within_window():
    assert rolodex.totp_remaining(0, 30) == 30       # exactly on a boundary → full window
    assert rolodex.totp_remaining(1, 30) == 29
    assert rolodex.totp_remaining(29, 30) == 1
    assert rolodex.totp_remaining(30, 30) == 30      # next window starts, full again


def test_decode_base32_is_tolerant_of_spaces_case_and_padding():
    raw = b"Hello!\xde\xad\xbe\xef"
    canonical = base64.b32encode(raw).decode()          # e.g. "JBSWY3DP54325MRQ"
    # lower-cased, space-grouped, and stripped of '=' padding — all should still decode.
    messy = canonical.rstrip("=").lower()
    messy = " ".join(messy[i:i + 4] for i in range(0, len(messy), 4))
    assert rolodex._decode_base32(messy) == raw


def test_decode_base32_returns_none_on_garbage():
    assert rolodex._decode_base32("not valid base32 !!!") is None
    assert rolodex._decode_base32("") is None


# --- detection rule: URI always, bare base32 only when the label hints 2FA ---

VALID_SEED = base64.b32encode(SEED_SHA1).decode()  # "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_parse_bare_base32_requires_a_2fa_label():
    # Same value, three labels: only the 2FA-hinting one becomes a TOTP field.
    assert rolodex.parse_totp_field("Authenticator", VALID_SEED) is not None
    assert rolodex.parse_totp_field("2FA secret", VALID_SEED) is not None
    assert rolodex.parse_totp_field("Password", VALID_SEED) is None


def test_parse_bare_base32_defaults():
    cfg = rolodex.parse_totp_field("Authenticator", VALID_SEED)
    assert cfg["secret"] == SEED_SHA1
    assert cfg["digits"] == 6
    assert cfg["period"] == 30
    assert cfg["algorithm"] == "sha1"


def test_parse_otpauth_uri_always_qualifies_regardless_of_label():
    uri = f"otpauth://totp/GitHub:me@example.com?secret={VALID_SEED}&issuer=GitHub"
    cfg = rolodex.parse_totp_field("Password", uri)  # label does NOT hint 2FA
    assert cfg is not None
    assert cfg["secret"] == SEED_SHA1
    assert cfg["digits"] == 6
    assert cfg["period"] == 30


def test_parse_otpauth_uri_honours_custom_parameters():
    uri = (f"otpauth://totp/Acme?secret={VALID_SEED}"
           "&algorithm=SHA256&digits=8&period=60")
    cfg = rolodex.parse_totp_field("whatever", uri)
    assert cfg["algorithm"] == "sha256"
    assert cfg["digits"] == 8
    assert cfg["period"] == 60


def test_parse_rejects_empty_and_non_totp():
    assert rolodex.parse_totp_field("Authenticator", "") is None
    assert rolodex.parse_totp_field("Authenticator", "   ") is None
    # HOTP (counter-based) is not TOTP — we only render time-based codes.
    assert rolodex.parse_totp_field("Password", f"otpauth://hotp/x?secret={VALID_SEED}") is None
    # 2FA label but the value isn't decodable base32 → no code, no crash.
    assert rolodex.parse_totp_field("Authenticator", "just some words") is None


def test_parse_otpauth_uri_missing_or_bad_secret_is_none():
    assert rolodex.parse_totp_field("x", "otpauth://totp/Acme?issuer=Acme") is None
    assert rolodex.parse_totp_field("x", "otpauth://totp/Acme?secret=!!!bad!!!") is None
