"""Static checks for the deploy files. Nothing here talks to Fly or Docker."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fly_toml_has_health_check_and_no_token() -> None:
    config = tomllib.loads((ROOT / "deploy" / "fly.toml").read_text(encoding="utf-8"))
    env = config["env"]
    assert "MUSE_BRIDGE_TOKEN" not in env
    assert env["MUSE_BRIDGE_PORT"] == "8080"
    assert env["MUSE_BRIDGE_DATA_DIR"] == "/data"
    assert config["http_service"]["internal_port"] == 8080
    assert config["http_service"]["checks"][0]["path"] == "/healthz"
    assert config["http_service"]["checks"][0]["method"] == "GET"
    assert config["mounts"][0]["destination"] == "/data"
    assert config["http_service"]["auto_stop_machines"] == "off"
    assert config["http_service"]["min_machines_running"] == 1


def test_dockerfile_copies_the_package_and_checks_health() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY muse_bridge" in text
    assert "HEALTHCHECK" in text
    assert "/healthz" in text
    assert "entrypoint.py" in text
    assert "MUSE_BRIDGE_TOKEN=" not in text


def test_entrypoint_drops_root_and_does_not_print_the_environment() -> None:
    text = (ROOT / "deploy" / "docker" / "entrypoint.py").read_text(encoding="utf-8")
    assert "setuid" in text
    assert "MUSE_BRIDGE_TOKEN" not in text
    assert "print(" not in text


def test_mac_placeholders_and_env_example() -> None:
    plist = (ROOT / "deploy" / "macmini" / "ai.nori.muse-bridge.plist").read_text(encoding="utf-8")
    assert "__MUSE_BRIDGE_ROOT__" in plist
    assert "__HOME__" in plist
    assert "/Users/" not in plist
    cloud = (ROOT / "deploy" / "macmini" / "cloudflared-config.yml").read_text(encoding="utf-8")
    assert "REPLACE_ME" in cloud
    assert "TUNNEL_UUID" in cloud
    assert "YOURDOMAIN" in cloud
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (
        "MUSE_BRIDGE_DATA_DIR",
        "MUSE_BRIDGE_LEGACY_SINGLE_TOKEN",
        "MUSE_BRIDGE_ALLOW_QUERY_KEY",
        "MUSE_BRIDGE_LIMITS_ENABLED",
        "MUSE_BRIDGE_RETENTION_DAYS",
        "MUSE_BRIDGE_PURGE_INTERVAL_SECONDS",
        "MUSE_BRIDGE_RATE_LIMIT",
        "MUSE_BRIDGE_RATE_WINDOW_SECONDS",
        "MUSE_BRIDGE_MAX_BODY_BYTES",
        "MUSE_BRIDGE_TOKEN",
        "MUSE_BRIDGE_WEBHOOK_URL",
        "MUSE_BRIDGE_WEBHOOK_SECRET",
    ):
        assert name in example
    assert "replace-with-openssl-rand-hex-24" in example
    for line in example.splitlines():
        if line.startswith("MUSE_BRIDGE_TOKEN="):
            assert line.endswith("replace-with-openssl-rand-hex-24")
