"""Shared request validation helpers for user credentials."""

import re

MAX_EMAIL_LENGTH = 254
MAX_EMAIL_LOCAL_PART_LENGTH = 64
MAX_EMAIL_DOMAIN_LENGTH = 253
MAX_EMAIL_DOMAIN_LABEL_LENGTH = 63
MAX_PASSWORD_LENGTH = 128
MIN_PASSWORD_LENGTH = 8
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$"
)
PASSWORD_SPECIAL_CHARACTERS = set(r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~""")


def validate_email_address(email: str) -> str:
    """Return a normalized email after enforcing the auth policy."""

    if email != email.strip():
        raise ValueError("email must not contain leading or trailing whitespace")

    if not email.isascii():
        raise ValueError("email must contain ASCII characters only")

    normalized_email = email.lower()
    if not EMAIL_PATTERN.fullmatch(normalized_email):
        raise ValueError("email must be a valid address")

    local_part, domain = normalized_email.split("@", 1)
    if len(local_part) > MAX_EMAIL_LOCAL_PART_LENGTH:
        raise ValueError("email local part is too long")

    if local_part.startswith(".") or local_part.endswith(".") or ".." in local_part:
        raise ValueError("email local part is invalid")

    if len(domain) > MAX_EMAIL_DOMAIN_LENGTH:
        raise ValueError("email domain is too long")

    domain_labels = domain.split(".")
    if any(len(label) > MAX_EMAIL_DOMAIN_LABEL_LENGTH for label in domain_labels):
        raise ValueError("email domain label is too long")

    if any(label.startswith("-") or label.endswith("-") for label in domain_labels):
        raise ValueError("email domain is invalid")

    return normalized_email


def validate_strong_password(password: str) -> str:
    """Return a password after enforcing the account password policy."""

    if password != password.strip():
        raise ValueError("password must not contain leading or trailing whitespace")

    if not password.isascii():
        raise ValueError("password must contain ASCII characters only")

    if any(character.isspace() for character in password):
        raise ValueError("password must not contain whitespace")

    if any(not character.isprintable() for character in password):
        raise ValueError("password must contain printable characters only")

    if not any(character.islower() for character in password):
        raise ValueError("password must contain a lowercase letter")

    if not any(character.isupper() for character in password):
        raise ValueError("password must contain an uppercase letter")

    if not any(character.isdigit() for character in password):
        raise ValueError("password must contain a digit")

    if not any(character in PASSWORD_SPECIAL_CHARACTERS for character in password):
        raise ValueError("password must contain a special character")

    return password
