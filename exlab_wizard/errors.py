"""Top-level exception hierarchy. Backend Section 4.3 errors.py."""

class ExLabError(Exception):
    """Base class for all ExLab-Wizard exceptions."""

class ConfigError(ExLabError):
    """Raised on config.yaml validation failures (Backend Section 9)."""

class ValidationError(ExLabError):
    """Raised by the validator engine on creation-time gates (Backend Section 8.1)."""

class TemplateLoadError(ExLabError):
    """Raised when a Copier template fails to load (Backend Section 5)."""

class TemplateCoreFieldRedeclaredError(TemplateLoadError):
    """Raised when a template redeclares label / operator / objective (Backend Section 10.3)."""

class PluginError(ExLabError):
    """Raised by plugin workers to signal expected failure (Backend Section 6)."""

class PluginInputRequired(ExLabError):
    """Raised by plugin workers to escalate for additional input (Backend Section 6.4)."""
    def __init__(self, fields: list, reason: str) -> None:
        super().__init__(reason)
        self.fields = fields
        self.reason = reason

class SchemaMajorMismatchError(ExLabError):
    """Raised when a cache file's schema major version exceeds reader support (Backend Section 11.9.2)."""
    def __init__(self, expected_major: int, found: str) -> None:
        super().__init__(f"schema major mismatch: expected {expected_major}, found {found}")
        self.expected_major = expected_major
        self.found = found

class KeyringUnavailableError(ExLabError):
    """Raised when no OS keyring backend is reachable AND fallback is exhausted (Backend Section 7.4.4)."""

class SetupIncompleteError(ExLabError):
    """Raised when a creation gate runs while setup state is INCOMPLETE_* (Backend Section 4.9)."""
