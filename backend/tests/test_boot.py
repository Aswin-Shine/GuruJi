"""OTP bypass must fail closed outside local dev."""
from unittest.mock import patch

import pytest

from app import main


def test_boot_refuses_bypass_in_non_local_env():
    with patch.object(main, "DEV_OTP_BYPASS", True), patch.object(main, "APP_ENV", "production"):
        with pytest.raises(RuntimeError, match="forbidden"):
            main._check_otp_bypass()


def test_boot_allows_bypass_in_local_env():
    with patch.object(main, "DEV_OTP_BYPASS", True), patch.object(main, "APP_ENV", "local"):
        main._check_otp_bypass()  # warns, does not raise


def test_boot_fine_with_bypass_off():
    with patch.object(main, "DEV_OTP_BYPASS", False), patch.object(main, "APP_ENV", "production"):
        main._check_otp_bypass()


def test_boot_refuses_default_secret_key_outside_local():
    """The default signs tokens AND derives parent PINs, and it is public."""
    with patch.object(main, "SECRET_KEY", main.DEFAULT_SECRET_KEY), \
         patch.object(main, "APP_ENV", "production"):
        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            main._check_secrets()


def test_boot_refuses_default_whatsapp_app_secret_outside_local():
    """The default lets anyone sign a webhook payload as any phone number."""
    with patch.object(main, "SECRET_KEY", "a-real-secret"), \
         patch.object(main, "WHATSAPP_APP_SECRET", main.DEFAULT_WHATSAPP_APP_SECRET), \
         patch.object(main, "APP_ENV", "production"):
        with pytest.raises(RuntimeError, match="WHATSAPP_APP_SECRET"):
            main._check_secrets()


def test_boot_names_every_placeholder_not_just_the_first():
    """Reporting one at a time means three deploy-fail-fix cycles instead of one."""
    with patch.object(main, "SECRET_KEY", main.DEFAULT_SECRET_KEY), \
         patch.object(main, "WHATSAPP_APP_SECRET", main.DEFAULT_WHATSAPP_APP_SECRET), \
         patch.object(main, "WHATSAPP_VERIFY_TOKEN", main.DEFAULT_WHATSAPP_VERIFY_TOKEN), \
         patch.object(main, "APP_ENV", "staging"):
        with pytest.raises(RuntimeError) as exc:
            main._check_secrets()
    for name in ("SECRET_KEY", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
        assert name in str(exc.value)


def test_boot_allows_default_secrets_in_local_env():
    """Local dev must stay frictionless — warn, never refuse."""
    with patch.object(main, "SECRET_KEY", main.DEFAULT_SECRET_KEY), \
         patch.object(main, "WHATSAPP_APP_SECRET", main.DEFAULT_WHATSAPP_APP_SECRET), \
         patch.object(main, "APP_ENV", "local"):
        main._check_secrets()


def test_boot_passes_when_secrets_are_real():
    with patch.object(main, "SECRET_KEY", "x8Jq-real"), \
         patch.object(main, "WHATSAPP_APP_SECRET", "y9Kr-real"), \
         patch.object(main, "WHATSAPP_VERIFY_TOKEN", "z0Ls-real"), \
         patch.object(main, "APP_ENV", "production"):
        main._check_secrets()


def _outbound(token, phone_id, version, env):
    return (
        patch.object(main, "WHATSAPP_ACCESS_TOKEN", token),
        patch.object(main, "WHATSAPP_PHONE_NUMBER_ID", phone_id),
        patch.object(main, "WHATSAPP_GRAPH_API_VERSION", version),
        patch.object(main, "APP_ENV", env),
    )


def test_boot_warns_when_outbound_unconfigured_outside_local(caplog):
    """Silence to every WhatsApp student must be visible at boot, not in a transcript."""
    a, b, c, d = _outbound("", "", "", "production")
    with a, b, c, d, caplog.at_level("WARNING", logger="guruji.boot"):
        main._check_whatsapp_outbound()  # warns, does not raise
    assert "outbound not configured" in caplog.text


def test_boot_quiet_when_outbound_unconfigured_in_local(caplog):
    a, b, c, d = _outbound("", "", "", "local")
    with a, b, c, d, caplog.at_level("WARNING", logger="guruji.boot"):
        main._check_whatsapp_outbound()
    assert "WHATSAPP" not in caplog.text


def test_boot_warns_on_partial_outbound_config_in_any_env(caplog):
    """Two of three keys set is a misconfiguration, not a choice — outbound is silently off."""
    a, b, c, d = _outbound("tok", "123", "", "local")
    with a, b, c, d, caplog.at_level("WARNING", logger="guruji.boot"):
        main._check_whatsapp_outbound()
    assert "WHATSAPP_GRAPH_API_VERSION" in caplog.text and "DISABLED" in caplog.text


def test_boot_warns_when_outbound_configured_in_local(caplog):
    """A dev box with a real token sends real messages to whatever number a test types."""
    a, b, c, d = _outbound("tok", "123", "v0.0", "local")
    with a, b, c, d, caplog.at_level("WARNING", logger="guruji.boot"):
        main._check_whatsapp_outbound()
    assert "REAL WhatsApp messages" in caplog.text


def test_boot_quiet_when_outbound_configured_in_production(caplog):
    a, b, c, d = _outbound("tok", "123", "v0.0", "production")
    with a, b, c, d, caplog.at_level("WARNING", logger="guruji.boot"):
        main._check_whatsapp_outbound()
    assert "WHATSAPP" not in caplog.text