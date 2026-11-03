"""Run the API as the chart's ServiceAccount, so RBAC is tested for real rather than assumed.

Applies the chart's own rbac.yaml and serviceaccount.yaml (release `cprbac`, so every cluster-scoped
name is unique), mints a short-lived token, and removes everything again at teardown.
"""
import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parents[4] / "charts" / "api-service"
RELEASE, SYS_NS = "cprbac", "cp-rbac-sys"


def run(*cmd: str, stdin: str | None = None) -> str:
    p = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} {cmd[1]} failed: {p.stderr.strip()[:400]}")
    return p.stdout


@dataclass
class ServiceAccountIdentity:
    manifest: str
    sa_name: str
    server: str
    ca_file: str
    token_file: str

    @property
    def subject(self) -> str:
        return f"system:serviceaccount:{SYS_NS}:{self.sa_name}"

    def can_i(self, verb: str, resource: str, namespace: str | None = None) -> bool:
        cmd = ["kubectl", "auth", "can-i", verb, resource, f"--as={self.subject}"]
        if namespace:
            cmd += ["-n", namespace]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return p.stdout.strip() == "yes"


def render(allowed_namespaces: list[str]) -> str:
    return run("helm", "template", RELEASE, str(CHART), "-n", SYS_NS,
               "--show-only", "templates/rbac.yaml", "--show-only", "templates/serviceaccount.yaml",
               "--set", "allowedNamespaces={" + ",".join(allowed_namespaces) + "}")


def provision(allowed_namespaces: list[str]) -> ServiceAccountIdentity:
    manifest = render(allowed_namespaces)
    sa = next(d["metadata"]["name"] for d in yaml.safe_load_all(manifest) if d and d["kind"] == "ServiceAccount")
    run("kubectl", "apply", "-f", "-", stdin=manifest)
    token = run("kubectl", "create", "token", sa, "-n", SYS_NS, "--duration=1h").strip()
    cluster = json.loads(run("kubectl", "config", "view", "--raw", "--minify", "-o", "json"))["clusters"][0]["cluster"]
    tmp = Path(tempfile.mkdtemp(prefix="cp-rbac-"))
    (tmp / "token").write_text(token)
    (tmp / "ca.crt").write_bytes(base64.b64decode(cluster["certificate-authority-data"]))
    for f in tmp.iterdir():
        f.chmod(0o600)
    return ServiceAccountIdentity(manifest, sa, cluster["server"], str(tmp / "ca.crt"), str(tmp / "token"))


def deprovision(identity: ServiceAccountIdentity) -> None:
    subprocess.run(["kubectl", "delete", "-f", "-", "--ignore-not-found", "--wait=false"],
                   input=identity.manifest, capture_output=True, text=True, timeout=120)
    for f in Path(identity.token_file).parent.iterdir():
        f.unlink()
    Path(identity.token_file).parent.rmdir()
