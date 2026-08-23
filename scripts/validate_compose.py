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
    rendered = interpolate(source, load_example_environment())
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
