"""Static Compose validation that does not require a local Docker daemon."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def load_example_environment() -> dict[str, str]:
    values: dict[str, str] = {"ENV_FILE": ".env.example"}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def interpolate(raw: str, values: dict[str, str]) -> str:
    escaped = raw.replace("$$", "\0")

    def replace(match: re.Match[str]) -> str:
        name, default = match.groups()
        value = values.get(name)
        if value is None or value == "":
            if default is None:
                raise ValueError(f"Missing Compose variable: {name}")
            return default
        return value

    return ENV_PATTERN.sub(replace, escaped).replace("\0", "$")


def main() -> None:
    source = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    values = load_example_environment()
    rendered = interpolate(source, values)
    if "${" in rendered:
        raise ValueError("Unresolved Compose interpolation remains")
    compose = yaml.safe_load(rendered)

    services = compose["services"]
    volumes = compose["volumes"]
    expected_services = {
        "postgres",
        "redis",
        "keycloak",
        "keycloak-provision",
        "paperless",
        "paperless-bootstrap",
        "ollama",
        "ollama-pull",
        "backend",
        "worker",
        "frontend",
        "reverse-proxy",
    }
    if set(services) != expected_services:
        raise ValueError(f"Unexpected services: {sorted(set(services) ^ expected_services)}")
    if any("ports" in service for name, service in services.items() if name != "reverse-proxy"):
        raise ValueError("Only reverse-proxy may publish host ports")
    if not compose["networks"]["data_net"].get("internal"):
        raise ValueError("Data network must remain internal")

    required_volumes = {
        "postgres_data",
        "redis_data",
        "paperless_data",
        "paperless_media",
        "paperless_consume",
        "paperless_export",
        "paperless_api_secret",
        "ollama_data",
        "export_data",
    }
    if set(volumes) != required_volumes:
        raise ValueError(f"Unexpected volumes: {sorted(set(volumes) ^ required_volumes)}")

    paperless = services["paperless"]
    if paperless["environment"]["PAPERLESS_DBUSER"] != "paperless":
        raise ValueError("Paperless must use its own DB user")
    if services["keycloak"]["environment"]["KC_DB_USERNAME"] != "keycloak":
        raise ValueError("Keycloak must use its own DB user")
    if services["backend"]["networks"] != ["app_net", "data_net"]:
        raise ValueError("Backend network isolation changed")
    if services["ollama"].get("profiles"):
        raise ValueError("Ollama must be part of the default Stage D stack")
    if services["worker"].get("depends_on", {}).get("ollama", {}).get("condition") != "service_healthy":
        raise ValueError("AI worker must wait for healthy Ollama")
    if services["worker"].get("depends_on", {}).get("ollama-pull", {}).get("condition") != "service_completed_successfully":
        raise ValueError("AI worker must wait for the configured model")
    if services["ollama"]["environment"].get("OLLAMA_NUM_PARALLEL") != "1":
        raise ValueError("Ollama parallelism must remain one")
    if str(services["worker"]["environment"].get("OLLAMA_NUM_GPU")) != "0":
        raise ValueError("Stage D must request CPU-only inference")
    if "approval:" not in values["DATABASE_URL"]:
        raise ValueError("Approval backend must use its dedicated database user")
    forbidden_approval_secrets = {"PAPERLESS_DB_PASSWORD", "KEYCLOAK_DB_PASSWORD"}
    for service_name in ("backend", "worker"):
        exposed = forbidden_approval_secrets.intersection(
            services[service_name].get("environment", {})
        )
        if exposed:
            raise ValueError(f"{service_name} receives foreign DB secrets: {sorted(exposed)}")

    images = {
        name: service["image"]
        for name, service in services.items()
        if "image" in service
    }
    print(f"Compose static validation: OK ({len(services)} services, {len(volumes)} volumes)")
    for name, image in sorted(images.items()):
        print(f"{name}: {image}")


if __name__ == "__main__":
    main()
