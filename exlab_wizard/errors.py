"""Top-level exception hierarchy. Backend Spec §4.3 errors.py."""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "ExLabError",
    "KeyringUnavailableError",
    "PluginError",
    "PluginInputRequired",
    "SchemaMajorMismatchError",
    "SetupIncompleteError",
    "TemplateCoreFieldRedeclaredError",
    "TemplateLoadError",
    "ValidationError",
]


class ExLabError(Exception):
    """Base class for all ExLab-Wizard exceptions."""


class ConfigError(ExLabError):
    """Raised on config.yaml validation failures. Backend Spec §9."""


class ValidationError(ExLabError):
    """Raised by the validator engine on creation-time gates. Backend Spec §8.1."""


class TemplateLoadError(ExLabError):
    """Raised when a Copier template fails to load. Backend Spec §5."""


class TemplateCoreFieldRedeclaredError(TemplateLoadError):
    """Raised when a template redeclares label / operator / objective. Backend Spec §10.3."""


class PluginError(ExLabError):
    """Raised by plugin workers to signal expected failure. Backend Spec §6."""


class PluginInputRequired(ExLabError):  # noqa: N818  -- name fixed by Backend Spec §6.4
    """Raised by plugin workers to escalate for additional input. Backend Spec §6.4."""

    def __init__(self, fields: list[dict], reason: str) -> None:
        super().__init__(reason)
        self.fields = fields

    @property
    def reason(self) -> str:
        return str(self)


class SchemaMajorMismatchError(ExLabError):
    """Raised when a cache file's schema major version exceeds reader support. Backend Spec §11.9.2."""

    def __init__(self, expected_major: int, found: str) -> None:
        super().__init__(f"schema major mismatch: expected {expected_major}, found {found}")
        self.expected_major = expected_major
        self.found = found


class KeyringUnavailableError(ExLabError):
    """Raised when no OS keyring backend is reachable and fallback is exhausted. Backend Spec §7.4.4."""


class SetupIncompleteError(ExLabError):
    """Raised when a creation gate runs while setup state is INCOMPLETE_*. Backend Spec §4.9."""
