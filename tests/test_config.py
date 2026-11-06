import json
from pathlib import Path

import pytest

from scrapewatch.config import Settings, config_hash, load_settings
from scrapewatch.errors import ConfigError

CONFIG = Path(__file__).parent / "fixtures" / "config"


def _golden(name: str) -> dict[str, object]:
    text = (CONFIG / name).read_text(encoding="utf-8")
    text = text.replace("{dir}", CONFIG.resolve().as_posix())
    data: dict[str, object] = json.loads(text)
    return data


def _as_posix_json(settings: Settings) -> dict[str, object]:
    raw = json.dumps(settings.model_dump(mode="json")).replace("\\\\", "/")
    data: dict[str, object] = json.loads(raw)
    return data


def test_full_file_matches_golden() -> None:
    settings = load_settings(CONFIG / "full.yml", env={})
    assert _as_posix_json(settings) == _golden("full.expected.json")


def test_defaults_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env={})
    assert settings == Settings()


def test_file_in_current_directory_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "scrapewatch.yml").write_text("name: here\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_settings(env={}).name == "here"


def test_env_overrides_file() -> None:
    env = {
        "SCRAPEWATCH_HISTORY__PATH": "/var/lib/sw",
        "SCRAPEWATCH_NAME": "from-env",
        "SCRAPEWATCH_COST__PROXIES__DATACENTER__PER_GB": "1.25",
        "SCRAPEWATCH_ALERTS__ON": "[warn, hold]",
        "SCRAPEWATCH_TELEGRAM_BOT_TOKEN": "not-a-setting",
        "OTHER": "ignored",
    }
    settings = load_settings(CONFIG / "full.yml", env=env)
    assert settings.history.path == Path("/var/lib/sw")
    assert settings.name == "from-env"
    assert settings.cost.proxies["datacenter"].per_gb == 1.25
    assert settings.cost.proxies["residential-a"].per_gb == 8.0  # untouched sibling
    assert settings.alerts.on == ["warn", "hold"]


def test_overrides_beat_env() -> None:
    env = {"SCRAPEWATCH_NAME": "from-env", "SCRAPEWATCH_HISTORY__KEEP_RUNS": "9"}
    settings = load_settings(
        CONFIG / "full.yml", env=env, overrides={"name": "explicit", "history.keep_runs": 3}
    )
    assert settings.name == "explicit"
    assert settings.history.keep_runs == 3


def test_scrapewatch_home_relocates_history() -> None:
    settings = load_settings(CONFIG / "full.yml", env={"SCRAPEWATCH_HOME": "/srv/sw"})
    assert settings.history.path == Path("/srv/sw")
    both = {"SCRAPEWATCH_HOME": "/srv/sw", "SCRAPEWATCH_HISTORY__PATH": "/srv/other"}
    assert load_settings(CONFIG / "full.yml", env=both).history.path == Path("/srv/other")


@pytest.mark.parametrize("name", ["bad_yaml.yml", "typo.yml", "missing.yml"])
def test_invalid_files_raise_config_error(name: str) -> None:
    with pytest.raises(ConfigError):
        load_settings(CONFIG / name, env={})


def test_non_mapping_file(tmp_path: Path) -> None:
    path = tmp_path / "list.yml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_settings(path, env={})


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "empty.yml"
    path.write_text("", encoding="utf-8")
    assert load_settings(path, env={}) == Settings()


def test_bad_env_value() -> None:
    with pytest.raises(ConfigError):
        load_settings(CONFIG / "full.yml", env={"SCRAPEWATCH_ALERTS__ON": "[unclosed"})
    with pytest.raises(ConfigError):
        load_settings(CONFIG / "full.yml", env={"SCRAPEWATCH_HISTORY__KEEP_RUNS": "many"})


def test_config_hash_is_stable_and_ignores_paths() -> None:
    a = load_settings(CONFIG / "full.yml", env={})
    b = load_settings(
        CONFIG / "full.yml",
        env={},
        overrides={"history.path": "/elsewhere", "report_dir": "/r", "rules": "/x.yml"},
    )
    assert config_hash(a) == config_hash(b)
    assert len(config_hash(a)) == 6
    int(config_hash(a), 16)
    c = load_settings(CONFIG / "full.yml", env={}, overrides={"cost.proxies.datacenter.per_gb": 2})
    assert config_hash(c) != config_hash(a)
