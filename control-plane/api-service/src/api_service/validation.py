"""Pure input checks (V1, V8). No I/O, no Kubernetes. Raise ValueError with a safe message.

Messages never echo the offending value (V7), because it may be an env value.
"""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

RESERVED_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})
MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
NAME_LABEL = "app.kubernetes.io/name"
RESERVED_LABEL_KEYS = frozenset({MANAGED_BY_LABEL, NAME_LABEL})
UID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_DNS_SUBDOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
_LABEL_NAME = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY = re.compile(r"^[-._a-zA-Z0-9]{1,253}$")
_PATH_COMPONENT = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*$")
_REGISTRY = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*(:[0-9]+)?$")
_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_QUANTITY = re.compile(r"^([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?$")

_SUFFIX = {
    "": Decimal(1), "m": Decimal("0.001"),
    "k": Decimal(10) ** 3, "M": Decimal(10) ** 6, "G": Decimal(10) ** 9,
    "T": Decimal(10) ** 12, "P": Decimal(10) ** 15, "E": Decimal(10) ** 18,
    "Ki": Decimal(2) ** 10, "Mi": Decimal(2) ** 20, "Gi": Decimal(2) ** 30,
    "Ti": Decimal(2) ** 40, "Pi": Decimal(2) ** 50, "Ei": Decimal(2) ** 60,
}


def check_dns_label(value: str, what: str = "value") -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 63 or not _DNS_LABEL.match(value):
        raise ValueError(f"{what} must be a DNS-1123 label: lowercase letters, digits and '-', "
                         "starting and ending with an alphanumeric, 1 to 63 characters")
    return value


def check_dns_subdomain(value: str, what: str = "value") -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 253 or not _DNS_SUBDOMAIN.match(value):
        raise ValueError(f"{what} must be a DNS-1123 subdomain")
    return value


def check_uid(value: str) -> str:
    if not isinstance(value, str) or not UID_RE.match(value):
        raise ValueError("id must be a UID (UUID format)")
    return value


@dataclass(frozen=True)
class ParsedImage:
    registry: str  # "docker.io" when the reference has none
    path: str
    tag: Optional[str]
    digest: Optional[str]


def parse_image(ref: str) -> ParsedImage:
    """Parse and check an image reference: [registry[:port]/]path[:tag][@sha256:hex] (V11, D4)."""
    if not isinstance(ref, str) or not ref or len(ref) > 255 or any(c.isspace() for c in ref):
        raise ValueError("image must be a non-empty reference without spaces, at most 255 characters")
    rest, digest, tag = ref, None, None
    if "@" in rest:
        rest, digest = rest.split("@", 1)
        if not _DIGEST.match(digest):
            raise ValueError("image digest must look like sha256:<64 hex characters>")
    if rest.rfind(":") > rest.rfind("/"):
        rest, tag = rest[: rest.rfind(":")], rest[rest.rfind(":") + 1:]
        if not _TAG.match(tag):
            raise ValueError("image tag is not valid")
    parts = rest.split("/")
    registry = "docker.io"
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry, parts = parts[0], parts[1:]
        if not _REGISTRY.match(registry):
            raise ValueError("image registry host is not valid")
    if not parts or not all(_PATH_COMPONENT.match(p) for p in parts):
        raise ValueError("image name must be lowercase letters, digits and the separators . _ -")
    if tag is None and digest is None:
        raise ValueError("image needs an explicit tag or digest")
    if tag == "latest" and digest is None:
        raise ValueError("image tag 'latest' is not allowed; use a pinned tag or digest")
    return ParsedImage(registry, "/".join(parts), tag, digest)


def parse_quantity(value: str) -> Decimal:
    """Parse a Kubernetes quantity such as 100m, 0.5, 128Mi. Must be greater than 0 (V14)."""
    if not isinstance(value, str):
        raise ValueError("quantity must be a string such as '100m' or '128Mi'")
    m = _QUANTITY.match(value)
    if not m:
        raise ValueError("quantity is not valid (examples: 100m, 0.5, 128Mi, 1Gi)")
    q = Decimal(m.group(1)) * _SUFFIX[m.group(2) or ""]
    if q <= 0:
        raise ValueError("quantity must be greater than 0")
    return q


def check_env_name(name: str) -> str:
    if not isinstance(name, str) or not _ENV_NAME.match(name):
        raise ValueError("environment variable name must match [A-Za-z_][A-Za-z0-9_]*")
    return name


def check_secret_key(key: str) -> str:
    if not isinstance(key, str) or not _SECRET_KEY.match(key):
        raise ValueError("secret key must be 1 to 253 characters of letters, digits, '-', '_' or '.'")
    return key


def check_label_key(key: str) -> str:
    prefix, _, name = key.rpartition("/")
    if prefix:
        check_dns_subdomain(prefix, "label key prefix")
    if not 1 <= len(name) <= 63 or not _LABEL_NAME.match(name):
        raise ValueError("label key name must be 1 to 63 characters of letters, digits, '-', '_' or '.'")
    if key in RESERVED_LABEL_KEYS:
        raise ValueError("this label key is reserved and set by the API")
    return key


def check_label_value(value: str) -> str:
    if len(value) > 63 or (value != "" and not _LABEL_NAME.match(value)):
        raise ValueError("label value must be at most 63 characters of letters, digits, '-', '_' or '.'")
    return value
