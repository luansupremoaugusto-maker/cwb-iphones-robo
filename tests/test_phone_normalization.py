from app.config import phone_variants


def test_phone_variants_bridge_brazilian_mobile_ninth_digit():
    assert phone_variants("+55 (41) 99611-4674") == (
        "5541996114674",
        "554196114674",
    )
    assert phone_variants("554196114674") == (
        "554196114674",
        "5541996114674",
    )


def test_phone_variants_leave_brazilian_landlines_unchanged():
    assert phone_variants("+55 (41) 3212-3456") == ("554132123456",)
