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