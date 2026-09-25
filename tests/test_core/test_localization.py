from app.config import Settings
import gettext
from string import Formatter

from babel.messages import pofile

from app.core.localization import LOCALES_DIR, t, tp, with_language_rule


def test_english_default_and_french_configuration(monkeypatch):
    monkeypatch.delenv("APP_LANGUAGE", raising=False)
    assert (
        Settings(_env_file=None, telegram_bot_token="token", intervals_api_key="key").app_language
        == "en"
    )
    assert (
        Settings(
            _env_file=None,
            telegram_bot_token="token",
            intervals_api_key="key",
            app_language="fr",
        ).app_language
        == "fr"
    )


def test_invalid_language_is_rejected():
    from pydantic import ValidationError

    try:
        Settings(
            _env_file=None, telegram_bot_token="token", intervals_api_key="key", app_language="de"
        )
    except ValidationError as error:
        assert "app_language" in str(error)
    else:
        raise AssertionError("Unsupported language was accepted")


def test_catalogs_have_same_keys_and_placeholders():
    catalogs = {}
    for language in ("en", "fr"):
        path = LOCALES_DIR / language / "LC_MESSAGES" / "banister.po"
        with path.open("rb") as source:
            catalog = pofile.read_po(source)
        catalogs[language] = {
            message.id: message.string for message in catalog if message.id
        }
    assert catalogs["en"].keys() == catalogs["fr"].keys()
    for key in catalogs["en"]:
        forms = []
        for language in ("en", "fr"):
            value = catalogs[language][key]
            strings = value if isinstance(value, tuple) else (value,)
            assert all(strings), key
            forms.append([
                {name for _, name, _, _ in Formatter().parse(item) if name}
                for item in strings
            ])
        assert forms[0] == forms[1], key
        if not forms[0][0] and not isinstance(catalogs["en"][key], tuple):
            assert t(key, language="en") == catalogs["en"][key]


def test_compiled_catalogs_match_sources():
    for language in ("en", "fr"):
        path = LOCALES_DIR / language / "LC_MESSAGES"
        with (path / "banister.po").open("rb") as source:
            catalog = pofile.read_po(source)
        with (path / "banister.mo").open("rb") as compiled:
            runtime = gettext.GNUTranslations(compiled)
        for message in catalog:
            if message.id and isinstance(message.id, str):
                assert runtime.gettext(message.id) == message.string


def test_plural_forms_follow_catalogs():
    assert tp("setup.days_confirm_button", 1, language="en") == "➡️ Confirm (1 day)"
    assert tp("setup.days_confirm_button", 2, language="en") == "➡️ Confirm (2 days)"
    assert tp("setup.days_confirm_button", 1, language="fr") == "➡️ Valider (1 jour)"
    assert tp("setup.days_confirm_button", 2, language="fr") == "➡️ Valider (2 jours)"


def test_language_file_can_enable_third_language(tmp_path, monkeypatch):
    from shutil import copyfile
    from app import config
    from app.core import localization

    target = tmp_path / "locales" / "de" / "LC_MESSAGES"
    target.mkdir(parents=True)
    copyfile(LOCALES_DIR / "en" / "LC_MESSAGES" / "banister.mo", target / "banister.mo")
    monkeypatch.setattr(config, "__file__", str(tmp_path / "app" / "config.py"))
    monkeypatch.setattr(localization, "LOCALES_DIR", tmp_path / "locales")
    localization._catalog.cache_clear()
    try:
        chosen = Settings(
            _env_file=None, telegram_bot_token="token", intervals_api_key="key", app_language="de"
        )
        assert chosen.app_language == "de"
        assert t("common.nothing_to_cancel", language="de") == "Nothing to cancel."
    finally:
        localization._catalog.cache_clear()


def test_language_rule_overrides_old_french_prompt():
    prompt = with_language_rule("Écris exclusivement en français.", language="en")
    assert prompt.endswith("this English output rule takes precedence.")
    assert "preserve names" in prompt
