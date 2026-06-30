from tender_ingest.relevance.rules import (
    NOISE_THRESHOLD,
    RELEVANT_THRESHOLD,
    score_subject,
)


def test_clear_design_tender_is_relevant() -> None:
    r = score_subject("Выполнение работ по подготовке проектной и рабочей документации")
    assert r.verdict == "relevant"
    assert r.score >= RELEVANT_THRESHOLD
    assert any("проект" in m.label.lower() for m in r.matched)


def test_restoration_okn_is_relevant() -> None:
    r = score_subject(
        "Разработка проектной документации по сохранению объекта культурного наследия"
    )
    assert r.verdict == "relevant"


def test_pure_supply_is_noise() -> None:
    r = score_subject("Поставка товара: медицинское оборудование и арматура")
    assert r.verdict == "noise"
    assert r.score <= NOISE_THRESHOLD
    assert r.anti_matched


def test_empty_subject_is_noise() -> None:
    r = score_subject("")
    assert r.verdict == "noise"
    assert r.score == 0
    assert r.matched == []


def test_word_boundary_pir_not_in_random_word() -> None:
    # «пирс»/«пирог» не должны давать вес ПИР
    r = score_subject("Поставка пирогов в столовую")
    assert not any(m.label == "ПИР" for m in r.matched)


def test_ambiguous_lands_in_maybe() -> None:
    # один слабый сигнал без явного перевеса -> maybe (порог между noise и relevant)
    r = score_subject("Авторский надзор за объектом")
    assert r.verdict == "maybe"


def test_strong_design_signal_survives_object_anti() -> None:
    # бюро проектирует склад с оборудованием: «поставка» в хвосте не должна убить
    # явный проектный тендер (анти описывает ОБЪЕКТ, а не суть закупки)
    r = score_subject(
        "Разработка проектной документации для поставки и монтажа оборудования склада"
    )
    assert r.verdict == "relevant"
    assert r.anti_matched  # анти сработало...
    assert r.matched  # ...но сильный проектный сигнал перевесил
