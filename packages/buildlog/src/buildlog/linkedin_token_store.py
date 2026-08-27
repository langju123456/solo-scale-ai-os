"""Secure local persistence for LinkedIn tokens and pending OAuth state."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from buildlog.linkedin_errors import (
    CredentialStoreError,
    MalformedTokenResponseError,
    OAuthStateMismatchError,
)
from buildlog.terminal_safety import is_unsafe_terminal_character

DEFAULT_CREDENTIALS_DIR = Path.home() / ".buildlog" / "credentials"
_BEARER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/-]+=*$")


class LinkedInToken(BaseModel):
    """Stored OAuth token with explicit expiry and redacted secret fields."""

    access_token: SecretStr = Field(min_length=1)
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    scopes: set[str] = Field(default_factory=set)
    scope_source: Literal["response", "unavailable"] = "unavailable"
    id_token: SecretStr | None = None
    refresh_token: SecretStr | None = None
    refresh_expires_at: datetime | None = None
    obtained_at: datetime

    @field_validator("access_token")
    @classmethod
    def validate_access_token(cls, token: SecretStr) -> SecretStr:
        """Reject values that cannot be placed safely in a bearer header."""
        value = token.get_secret_value()
        if _BEARER_TOKEN_PATTERN.fullmatch(value) is None:
            raise ValueError("OAuth access token contains invalid characters")
        return token

    @field_validator("id_token", "refresh_token")
    @classmethod
    def validate_optional_tokens(cls, token: SecretStr | None) -> SecretStr | None:
        """Reject control characters in optional provider credential values."""
        if token is not None and any(
            is_unsafe_terminal_character(character)
            for character in token.get_secret_value()
        ):
            raise ValueError("OAuth token contains invalid control characters")
        return token

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, scopes: set[str]) -> set[str]:
        """Reject malformed scope values before they reach terminal output."""
        if any(
            re.fullmatch(r"[A-Za-z0-9._-]+", scope) is None
            for scope in scopes
        ):
            raise ValueError("OAuth scopes contain invalid characters")
        return scopes

    @field_validator("expires_at", "refresh_expires_at", "obtained_at")
    @classmethod
    def validate_timestamps(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        """Reject ambiguous local credential timestamps."""
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("OAuth token timestamps must include a timezone")
        return value

    def is_expired(
        self,
        *,
        now: datetime | None = None,
        skew_seconds: int = 30,
    ) -> bool:
        """Return whether the token is expired or inside a safety window."""
        current = now or datetime.now(UTC)
        expires_at = _as_utc(self.expires_at)
        return current >= expires_at - timedelta(seconds=skew_seconds)

    def storage_payload(self) -> dict[str, object]:
        """Return only credentials required by the implemented local workflow."""
        return {
            "access_token": self.access_token.get_secret_value(),
            "token_type": self.token_type,
            "expires_at": self.expires_at.isoformat(),
            "scopes": sorted(self.scopes),
            "scope_source": self.scope_source,
            "obtained_at": self.obtained_at.isoformat(),
        }


class TokenStore(Protocol):
    """Persistence boundary for LinkedIn OAuth tokens."""

    @property
    def path(self) -> Path:
        """Return the local token path."""

    def load(self) -> LinkedInToken | None:
        """Load the token or return ``None`` when logged out."""

    def save(self, token: LinkedInToken) -> None:
        """Persist a token atomically."""

    def delete(self) -> bool:
        """Delete the token and return whether one existed."""


class FileTokenStore:
    """Atomic, permission-restricted user-level token storage."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CREDENTIALS_DIR / "linkedin.json"

    @property
    def path(self) -> Path:
        """Return the local token path."""
        return self._path

    def load(self) -> LinkedInToken | None:
        """Load and validate the stored token."""
        _require_safe_credential_parent(self._path.parent)
        if self._path.is_symlink():
            raise CredentialStoreError(
                "The local LinkedIn token file must not be a symbolic link."
            )
        if not self._path.exists():
            return None
        _validate_private_token_file(self._path)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return LinkedInToken.model_validate(payload)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise MalformedTokenResponseError(
                "The local LinkedIn token file is invalid. Run the logout command "
                "and authorize again."
            ) from exc

    def save(self, token: LinkedInToken) -> None:
        """Persist a token through an atomic same-directory replacement."""
        try:
            _atomic_private_json_write(self._path, token.storage_payload())
        except OSError as exc:
            raise CredentialStoreError(
                "The local LinkedIn token could not be stored securely."
            ) from exc

    def delete(self) -> bool:
        """Delete the token file without affecting BuildLog runs."""
        _require_safe_credential_parent(self._path.parent)
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CredentialStoreError(
                "The local LinkedIn token could not be deleted."
            ) from exc
        return True


class OAuthStateStore(Protocol):
    """One-time persistence boundary for OAuth CSRF state."""

    def save(self, state: str, *, created_at: datetime) -> None:
        """Persist a hash of pending state."""

    def consume(
        self,
        returned_state: str,
        *,
        now: datetime,
        max_age_seconds: int = 600,
    ) -> None:
        """Validate and consume pending state."""

    def delete(self) -> None:
        """Delete pending state."""


class FileOAuthStateStore:
    """Store only a one-time SHA-256 state hash in the credential directory."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CREDENTIALS_DIR / "linkedin-oauth-state.json"

    def save(self, state: str, *, created_at: datetime) -> None:
        """Persist the state hash atomically with private permissions."""
        try:
            _atomic_private_json_write(
                self._path,
                {
                    "state_hash": _state_hash(state),
                    "created_at": created_at.isoformat(),
                },
            )
        except OSError as exc:
            raise CredentialStoreError(
                "The pending LinkedIn OAuth state could not be stored securely."
            ) from exc

    def consume(
        self,
        returned_state: str,
        *,
        now: datetime,
        max_age_seconds: int = 600,
    ) -> None:
        """Validate state in constant time and delete it after one use."""
        _require_safe_credential_parent(self._path.parent)
        if self._path.exists():
            _validate_private_state_file(self._path)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("OAuth state payload must be an object")
            expected_hash = payload.get("state_hash")
            raw_created_at = payload.get("created_at")
            if (
                not isinstance(expected_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
                or not isinstance(raw_created_at, str)
            ):
                raise ValueError("OAuth state payload is malformed")
            created_at = datetime.fromisoformat(raw_created_at)
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("OAuth state timestamp must include a timezone")
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.delete()
            raise OAuthStateMismatchError(
                "No valid pending LinkedIn login was found. Run login again."
            ) from exc

        self.delete()
        age = _as_utc(now) - _as_utc(created_at)
        valid_hash = hmac.compare_digest(
            expected_hash,
            _state_hash(returned_state),
        )
        if not valid_hash or age < timedelta(0) or age > timedelta(
            seconds=max_age_seconds
        ):
            raise OAuthStateMismatchError(
                "LinkedIn OAuth state was invalid or expired. Run login again."
            )

    def delete(self) -> None:
        """Delete pending state if it exists."""
        _require_safe_credential_parent(self._path.parent)
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CredentialStoreError(
                "The pending LinkedIn OAuth state could not be deleted."
            ) from exc


def _atomic_private_json_write(path: Path, payload: dict[str, object]) -> None:
    _validate_credential_parent(path.parent)
    if path.is_symlink():
        raise OSError("credential file path must not be a symbolic link")
    if path.exists() and not path.is_file():
        raise OSError("credential file path is not a regular file")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    else:
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    _validate_credential_parent(path.parent)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    descriptor_open = True
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor_open = False
        with handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if descriptor_open:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_credential_parent(parent: Path) -> None:
    for candidate in (parent, parent.parent):
        if candidate.is_symlink():
            raise OSError(
                "credential directory path must not contain a symbolic link"
            )
        if candidate.exists() and not candidate.is_dir():
            raise OSError("credential directory path is not a directory")
    if os.name == "posix" and parent.parent.exists():
        container_permissions = stat.S_IMODE(parent.parent.stat().st_mode)
        if container_permissions & 0o022:
            raise OSError(
                "credential directory parent allows group or other writes"
            )


def _validate_private_token_file(path: Path) -> None:
    _require_safe_credential_parent(path.parent)
    if path.is_symlink():
        raise CredentialStoreError(
            "The local LinkedIn token file must not be a symbolic link."
        )
    if not path.is_file():
        raise CredentialStoreError(
            "The local LinkedIn token path is not a regular file."
        )
    if os.name == "posix":
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise CredentialStoreError(
                "The local LinkedIn token file permissions are unsafe. "
                "Run chmod 600 on the file or log out and authorize again."
            )


def _validate_private_state_file(path: Path) -> None:
    if path.is_symlink():
        raise CredentialStoreError(
            "The pending LinkedIn OAuth state file must not be a symbolic link."
        )
    if not path.is_file():
        raise CredentialStoreError(
            "The pending LinkedIn OAuth state path is not a regular file."
        )
    if os.name == "posix":
        permissions = stat.S_IMODE(path.stat().st_mode)
        if permissions & 0o077:
            raise CredentialStoreError(
                "The pending LinkedIn OAuth state file permissions are unsafe."
            )


def _require_safe_credential_parent(parent: Path) -> None:
    try:
        _validate_credential_parent(parent)
        if os.name == "posix" and parent.exists():
            permissions = stat.S_IMODE(parent.stat().st_mode)
            if permissions & 0o077:
                raise OSError(
                    "credential directory permissions allow group or other access"
                )
    except OSError as exc:
        raise CredentialStoreError(
            "The local LinkedIn credential directory is unsafe, or its parent "
            "is unsafe. Restrict parent write access, then run chmod 700 on the "
            "credential directory or remove it and authorize again."
        ) from exc


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
