"""Smoke tests de los assets de Fase D.

No requieren Docker daemon: solo verifican que los archivos existen, que
el compose es YAML valido y declara los servicios/healthchecks/volumenes
esperados, y que el Dockerfile referencia entrypoint correcto.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_exists_with_multistage():
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.11-slim AS builder" in text
    assert "FROM python:3.11-slim AS runtime" in text
    assert "scripts/migrate.sh" in text
    assert "uvicorn main:app" in text
    assert "USER app" in text  # non-root
    assert "HEALTHCHECK" in text


def test_compose_yaml_valid_and_has_required_services():
    try:
        import yaml
    except ImportError:
        # pyyaml viene transitivamente con muchos paquetes; si no esta,
        # parsear a mano lo minimo seria fragil. Skipear con asserts basicos.
        text = (ROOT / "docker-compose.yml").read_text()
        assert "postgres:" in text and "web:" in text
        assert "pgdata" in text and "appdata" in text
        assert "service_healthy" in text
        return
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = data["services"]
    assert set(services.keys()) >= {"postgres", "web"}
    # postgres: healthcheck + volumen + NO ports por seguridad
    assert "healthcheck" in services["postgres"]
    assert services["postgres"].get("ports") in (None, [])
    assert any("pgdata" in v for v in services["postgres"]["volumes"])
    # web: depends_on healthy + healthcheck + volumen appdata + restart
    assert services["web"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["web"]["restart"] == "unless-stopped"
    assert any("appdata" in v for v in services["web"]["volumes"])
    assert "healthcheck" in services["web"]
    # volumenes declarados
    assert {"pgdata", "appdata"} <= set(data["volumes"].keys())


def test_makefile_has_required_targets():
    text = (ROOT / "Makefile").read_text()
    for tgt in ("up:", "down:", "logs:", "migrate:", "test:", "shell:",
                "psql:", "smoke:", "clean:"):
        assert tgt in text, f"falta target {tgt} en Makefile"


def test_healthcheck_script_is_executable():
    p = ROOT / "scripts" / "healthcheck.py"
    assert p.exists()
    assert (p.stat().st_mode & 0o111), "healthcheck.py no es ejecutable"


def test_dockerignore_excludes_env_and_git():
    text = (ROOT / ".dockerignore").read_text()
    for needed in (".env", ".git", "__pycache__", ".pytest_cache"):
        assert needed in text, f"{needed} no esta en .dockerignore"


def test_gitignore_excludes_compose_override():
    text = (ROOT / ".gitignore").read_text()
    assert "docker-compose.override.yml" in text
    assert ".env" in text


def test_procfile_still_exists_for_railway():
    """Railway sigue siendo path soportado: no eliminamos Procfile."""
    p = ROOT / "Procfile"
    assert p.exists(), "Procfile borrado: Railway dejaria de funcionar"


def test_env_example_has_compose_vars_but_no_secrets():
    text = (ROOT / ".env.example").read_text()
    assert "POSTGRES_USER" in text and "POSTGRES_PASSWORD" in text
    assert "POSTGRES_DB" in text and "WEB_PORT" in text
    # placeholder, no contrasena real
    assert "change_me" in text
