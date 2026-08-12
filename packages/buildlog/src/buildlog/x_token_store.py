"""Private local storage for X OAuth tokens and PKCE state."""

from __future__ import annotations

import hmac
import json
import os
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

from buildlog.terminal_safety import is_unsafe_terminal_character
from buildlog.x_errors import XCredentialStoreError, XOAuthError

DEFAULT_CREDENTIALS_DIR = Path.home() / ".buildlog" / "credentials"


class XToken(BaseModel):
    """Stored X OAuth user token with redacted credential fields."""

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = None
    token_type: str = "Bearer"
    expires_at: datetime
    obtained_at: datetime
    scopes: set[str] = Field(default_factory=set)

    @field_validator("access_token", "refresh_token")
    @classmethod
    def validate_secret(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        """Reject credentials that cannot be stored or transmitted safely."""
        if value is not None and any(
            is_unsafe_terminal_character(character)
            for character in value.get_secret_value()
        ):
            raise ValueError("OAuth token contains invalid control characters")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, value: str) -> str:
        """Accept only bearer user tokens."""
        if value.casefold() != "bearer":
            raise ValueError("X OAuth token_type must be Bearer")
        return "Bearer"

    @field_validator("expires_at", "obtained_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        """Require timezone-aware token timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("X OAuth timestamps must include a timezone")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: set[str]) -> set[str]:
        """Keep provider scopes safe for diagnostics."""
        if any(
            not value
            or any(
                is_unsafe_terminal_character(character)
                or character.isspace()
                for character in value
            )
            for value in values
        ):
            raise ValueError("X OAuth scopes contain invalid characters")
        return values

    def is_expired(
        self,
        *,
        now: datetime | None = None,
        skew_seconds: int = 30,
    ) -> bool:
        """Return whether the access token is expired or nearly expired."""
        current = now or datetime.now(UTC)
        return current >= self.expires_at.astimezone(UTC) - timedelta(
            seconds=skew_seconds
        )

    def storage_payload(self) -> dict[str, object]:
        """Return the provider fields needed by the local workflow."""
        return {
            "access_token": self.access_token.get_secret_value(),
            "refresh_token": (
                self.refresh_token.get_secret_value()
                if self.refresh_token is not None
                else None
            ),
            "token_type": self.token_type,
            "expires_at": self.expires_at.isoformat(),
            "obtained_at": self.obtained_at.isoformat(),
            "scopes": sorted(self.scopes),
        }


class FileXTokenStore:
    """Atomic, private storage for one X user token."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CREDENTIALS_DIR / "x.json"

    @property
    def path(self) -> Path:
        """Return the local token file path."""
        return self._path

    def load(self) -> XToken | None:
        """Load and validate the stored token."""
        if not self._path.exists():
            return None
        _validate_private_file(self._path)
        try:
            return XToken.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise XCredentialStoreError(
                "The local X token is invalid. Run `buildlog x logout` and "
                "authorize again."
            ) from exc

    def save(self, token: XToken) -> None:
        """Persist one token through atomic replacement."""
        _write_private_json(self._path, token.storage_payload())

    def delete(self) -> bool:
        """Delete the local token and return whether one existed."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise XCredentialStoreError(
                "The local X token could not be deleted."
            ) from exc
        return True


class PendingXAuthorization(BaseModel):
    """One private PKCE verifier bound to an OAuth state."""

    state: SecretStr = Field(min_length=16)
    code_verifier: SecretStr = Field(min_length=43, max_length=128)
    created_at: datetime


class FileXAuthorizationStore:
    """Persist one short-lived PKCE authorization between browser steps."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_CREDENTIALS_DIR / "x-oauth-state.json"

    def save(self, pending: PendingXAuthorization) -> None:
        """Persist a pending authorization privately."""
        _write_private_json(
            self._path,
            {
                "state": pending.state.get_secret_value(),
                "code_verifier": pending.code_verifier.get_secret_value(),
                "created_at": pending.created_at.isoformat(),
            },
        )

    def consume(
        self,
        returned_state: str,
        *,
        now: datetime,
        max_age_seconds: int = 600,
    ) -> PendingXAuthorization:
        """Validate and consume one OAuth state value."""
        if not self._path.exists():
            raise XOAuthError("No pending X login exists. Run login again.")
        _validate_private_file(self._path)
        try:
            pending = PendingXAuthorization.model_validate_json(
                self._path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            self.delete()
            raise XOAuthError(
                "The pending X login is invalid. Run login again."
            ) from exc
        self.delete()
        age = now.astimezone(UTC) - pending.created_at.astimezone(UTC)
        if (
            not hmac.compare_digest(
                pending.state.get_secret_value(),
                returned_state,
            )
            or age < timedelta(0)
            or age > timedelta(seconds=max_age_seconds)
        ):
            raise XOAuthError("X OAuth state was invalid or expired. Run login again.")
        return pending

    def delete(self) -> None:
        """Delete pending state if present."""
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise XCredentialStoreError(
                "The pending X OAuth state could not be deleted."
            ) from exc


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise XCredentialStoreError("The X credential path is unsafe.")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise XCredentialStoreError(
            "The X credential could not be stored privately."
        ) from exc


def _validate_private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise XCredentialStoreError("The X credential path is unsafe.")
    if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise XCredentialStoreError(
            f"The X credential permissions are unsafe. Run chmod 600 {path}."
        )
