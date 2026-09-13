from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "relative_path",
    [
        "deploy/Caddyfile",
        "deploy/Caddyfile.production",
    ],
)
def test_gateway_applies_minimum_browser_security_headers(relative_path):
    caddyfile = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    assert "Content-Security-Policy" in caddyfile
    assert "default-src 'self'" in caddyfile
    assert "frame-ancestors 'none'" in caddyfile
    assert "Permissions-Policy" in caddyfile
    assert "camera=(self)" in caddyfile
    assert "microphone=(self)" in caddyfile
    assert "camera=()" not in caddyfile
    assert 'X-Frame-Options "DENY"' in caddyfile
    assert 'X-Content-Type-Options "nosniff"' in caddyfile


def test_tunnel_gateway_enables_hsts_only_for_the_public_hostname():
    caddyfile = (PROJECT_ROOT / "deploy/Caddyfile").read_text(encoding="utf-8")

    assert "@publichost host {$CHAT_PUBLIC_HOST:chat.example.invalid}" in caddyfile
    assert "{$EXTERNAL_DEV_HOST:external-dev.invalid}" in caddyfile
    assert (
        'header @publichost >Strict-Transport-Security '
        '"max-age=31536000; includeSubDomains"'
    ) in caddyfile


def test_production_gateway_enables_hsts():
    caddyfile = (PROJECT_ROOT / "deploy/Caddyfile.production").read_text(
        encoding="utf-8"
    )

    assert (
        '>Strict-Transport-Security "max-age=31536000; includeSubDomains"'
        in caddyfile
    )


def test_external_dev_hides_passwordless_reviewer_and_debug_routes():
    caddyfile = (PROJECT_ROOT / "deploy/Caddyfile").read_text(encoding="utf-8")

    assert "@external_dev_passwordless_reviewer" in caddyfile
    assert "/reviewer /reviewer/* /api/auth/reviewer-session" in caddyfile
    assert "respond @external_dev_passwordless_reviewer 404" in caddyfile
    assert "/api/docs /api/docs/* /api/openapi.json" in caddyfile
    assert "@external_dev_health" in caddyfile
    assert '`{"status":"ok"}` 200' in caddyfile


@pytest.mark.parametrize(
    "relative_path",
    [
        "deploy/Caddyfile",
        "deploy/Caddyfile.production",
    ],
)
def test_dynamic_shell_cache_headers_replace_upstream_values(relative_path):
    caddyfile = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

    assert caddyfile.count('>Cache-Control "no-store, max-age=0"') == 4
    assert '>Content-Type "application/manifest+json"' in caddyfile
    assert '>Service-Worker-Allowed "/"' in caddyfile
    assert "@appversion path /app-version.json" in caddyfile
    assert "@html header Accept *text/html*" in caddyfile
    assert "header @manifest Cache-Control" not in caddyfile
    assert "header @serviceworker Cache-Control" not in caddyfile
