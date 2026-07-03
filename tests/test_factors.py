from decimal import Decimal
from types import SimpleNamespace

from tender_ingest.relevance.factors import compute_factors, hard_exclusion, is_auction
from tender_ingest.relevance.scorer import verdict_from_score


def _card(**kw: object) -> SimpleNamespace:
    base = {
        "law": "44-ФЗ",
        "purchase_method": "Открытый конкурс в электронной форме",
        "advance_raw": "20.00 %",
        "nmck": Decimal("8000000"),
        "region_code": "72",
        "customer_name": "Администрация города Тюмени",
        "customer_inn": "7202000000",
        "stage": "Подача заявок",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_ideal_card_factors() -> None:
    f = compute_factors(_card())
    assert f.method_kind == "конкурс"
    assert f.has_advance is True
    assert f.nmck_in_band is True
    assert f.region_priority is True
    assert f.customer_excluded is False
    assert f.stage_active is True
    assert hard_exclusion(f) is None


def test_auction_is_soft_not_hard() -> None:
    f = compute_factors(_card(purchase_method="Электронный аукцион"))
    assert f.method_kind == "аукцион"
    assert is_auction(f) is True
    assert hard_exclusion(f) is None  # аукцион не жёсткое исключение (мягко откладываем)


def test_excluded_customer() -> None:
    assert hard_exclusion(compute_factors(_card(customer_name="ПАО «Россети Урал»"))) is not None
    assert hard_exclusion(compute_factors(_card(customer_name="АО «ЕЭСК»"))) is not None


def test_inn_blacklist() -> None:
    blacklist = frozenset({"7202000000"})
    f = compute_factors(_card(customer_inn="7202000000"), blacklist)
    assert f.inn_blacklisted is True
    assert "ИНН" in (hard_exclusion(f) or "")
    # тот же ИНН, но пустой стоп-лист -> не исключаем
    assert compute_factors(_card(customer_inn="7202000000")).inn_blacklisted is False
    # другой ИНН при непустом стоп-листе -> не исключаем
    assert compute_factors(_card(customer_inn="6600000000"), blacklist).inn_blacklisted is False


def test_inactive_stage_hard_excluded() -> None:
    assert "этап" in (hard_exclusion(compute_factors(_card(stage="Работа комиссии"))) or "")
    assert hard_exclusion(compute_factors(_card(stage="Подача заявок"))) is None


def test_nmck_hard_bounds() -> None:
    assert hard_exclusion(compute_factors(_card(nmck=Decimal("1000000")))) is not None  # < 1.5 млн
    assert (
        hard_exclusion(compute_factors(_card(nmck=Decimal("200000000")))) is not None
    )  # > 180 млн
    assert hard_exclusion(compute_factors(_card(nmck=Decimal("8000000")))) is None  # в норме


def test_price_band_and_region() -> None:
    assert compute_factors(_card(nmck=Decimal("1800000"))).nmck_in_band is False  # 1.5–2: не в band
    assert compute_factors(_card(region_code="77")).region_priority is False  # Москва
    assert compute_factors(_card(region_code="66")).region_priority is True  # Свердловская


def test_verdict_from_score() -> None:
    assert verdict_from_score(80) == "relevant"
    assert verdict_from_score(60) == "relevant"
    assert verdict_from_score(45) == "maybe"
    assert verdict_from_score(35) == "maybe"
    assert verdict_from_score(10) == "noise"
    assert verdict_from_score(0) == "noise"
