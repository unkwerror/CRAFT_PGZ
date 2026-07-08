"""Канонический каталог разделов работ и нормализатор названий.

Одни и те же работы в таблице «Экономика» названы по-разному («ПЗУ», «ПЗУ(ГП)»,
«Раздел 2. Схема планировочной организации земельного участка», «Генеральный план»).
Чтобы собирать статистику долей по разделам, названия приводятся к каноническому ключу:
сначала точный алиас, затем упорядоченные keyword-правила (первое совпадение побеждает).
Каталог и правила построены по ~540 реальным названиям строк из файла «Экономика (2).xlsx».
Не распозналось -> None (строка остаётся в базе без канона, LLM может сматчить позже).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Группы: management — руководство проектом; overhead — накладные бюро;
# design — разделы ПД/РД; survey — изыскания и обследования; heritage — работы по ОКН;
# expertise — экспертизы/согласования; other — прочее.


@dataclass(frozen=True)
class CanonSection:
    key: str
    label: str
    group: str


CATALOG: tuple[CanonSection, ...] = (
    # --- управление проектом ---
    CanonSection("gip", "ГИП (сопровождение ПСД)", "management"),
    CanonSection("gap", "ГАП / руководитель архитектурного проектирования", "management"),
    CanonSection("project_lead", "Руководитель проекта", "management"),
    CanonSection("project_manager", "Менеджер проекта", "management"),
    CanonSection("project_admin", "Администратор проекта (помощник ГИПа)", "management"),
    CanonSection("bim_manager", "BIM-менеджер", "management"),
    CanonSection("normokontrol", "Нормоконтроль", "management"),
    # --- накладные бюро ---
    CanonSection(
        "freelance_check", "Проверка с/с работы фрилансеров, выпуск документации", "overhead"
    ),
    CanonSection("reserve", "Резерв", "overhead"),
    CanonSection("office", "Содержание офиса", "overhead"),
    CanonSection("print_docs", "Печать документации", "overhead"),
    CanonSection("travel", "Командировки", "overhead"),
    CanonSection("approvals", "Согласование документации", "overhead"),
    CanonSection("tz_dev", "Разработка ТЗ совместно с заказчиком", "overhead"),
    # --- разделы ПД/РД (ПП-87) ---
    CanonSection("pz", "ПЗ — пояснительная записка", "design"),
    CanonSection("pzu", "ПЗУ / генеральный план", "design"),
    CanonSection("ar", "АР — архитектурные решения", "design"),
    CanonSection("kr", "КР (АС) — конструктивные решения", "design"),
    CanonSection("ar_kr", "АР+КР совместно", "design"),
    CanonSection("ios1", "ИОС1 — электроснабжение, электроосвещение", "design"),
    CanonSection("ios2", "ИОС2 — водоснабжение", "design"),
    CanonSection("ios3", "ИОС3 — водоотведение, ливневая канализация", "design"),
    CanonSection("ios2_3", "ИОС2+ИОС3 — ВК/НВК совместно", "design"),
    CanonSection("ios4", "ИОС4 — отопление, вентиляция, тепловые сети", "design"),
    CanonSection("ios5", "ИОС5 — сети связи, слаботочные системы", "design"),
    CanonSection("ios6", "ИОС6 — газоснабжение", "design"),
    CanonSection("tx", "ТХ (ТР) — технологические решения", "design"),
    CanonSection("pos", "ПОС — проект организации строительства", "design"),
    CanonSection("pod", "ПОД — проект организации демонтажа", "design"),
    CanonSection("por", "ПОР — проект организации работ", "design"),
    CanonSection("oos", "ООС — охрана окружающей среды", "design"),
    CanonSection("waste_reg", "Техрегламент обращения с отходами строительства", "design"),
    CanonSection("pb", "ПБ — пожарная безопасность", "design"),
    CanonSection("odi", "ОДИ — доступ маломобильных групп", "design"),
    CanonSection("tbe", "ТБЭ — безопасная эксплуатация", "design"),
    CanonSection("gochs", "ГОЧС", "design"),
    CanonSection("smeta", "СМ — сметная документация", "design"),
    CanonSection("other_docs", "Иная документация (раздел 12)", "design"),
    CanonSection("ee", "ЭЭ — энергоэффективность", "design"),
    CanonSection("stu", "СТУ — специальные технические условия", "design"),
    CanonSection("ep", "ЭП — эскизный проект", "design"),
    CanonSection("viz", "Визуализация / графический дизайн", "design"),
    CanonSection("design_project", "Дизайн-проект", "design"),
    CanonSection("landscape", "Ландшафтные решения", "design"),
    # агрегаты стадий: ТЗ иногда пишет «ПД»/«РД» целиком без пораздельной детализации;
    # прямой статистики в базе нет — движок оценивает производно от design-группы аналогов
    CanonSection("pd_total", "ПД целиком (агрегат, состав не расписан)", "design"),
    CanonSection("rd_total", "РД целиком (агрегат)", "design"),
    CanonSection("pd_rd_total", "ПД+РД целиком (агрегат)", "design"),
    # --- изыскания и обследования ---
    CanonSection("surveys_all", "Инженерные изыскания (комплекс)", "survey"),
    CanonSection("igdi", "ИГДИ — геодезические изыскания", "survey"),
    CanonSection("igi", "ИГИ — геологические/геотехнические изыскания", "survey"),
    CanonSection("iei", "ИЭИ — экологические изыскания", "survey"),
    CanonSection("igmi", "ИГМИ — гидрометеорологические изыскания", "survey"),
    CanonSection("iti", "ИТИ — обследование конструкций", "survey"),
    CanonSection("obmery", "Обмеры / обмерные чертежи", "survey"),
    CanonSection("scan3d", "3D-сканирование", "survey"),
    # --- работы по ОКН ---
    CanonSection("npd", "НПД — научно-проектная документация", "heritage"),
    CanonSection("ihti", "ИХТИ — химико-технологические исследования", "heritage"),
    CanonSection("shurfy", "Шурфы (земляные работы)", "heritage"),
    CanonSection("labs", "Лабораторные исследования", "heritage"),
    CanonSection("poverka", "Поверочные расчёты", "heritage"),
    CanonSection("iabi", "ИАБИ — историко-архивные исследования", "heritage"),
    CanonSection("kni", "КНИ — комплексные научные исследования", "heritage"),
    CanonSection("okni", "ОКНИ — отчёт по КНИ", "heritage"),
    CanonSection("ff", "Фотофиксация", "heritage"),
    CanonSection("archeology", "Археология / историко-культурные изыскания", "heritage"),
    CanonSection("arch_spravka", "Архивная справка", "heritage"),
    CanonSection("mos", "МОС — обеспечение сохранности ОКН", "heritage"),
    CanonSection("po_okn", "Предмет охраны ОКН", "heritage"),
    CanonSection("gike", "ГИКЭ — историко-культурная экспертиза", "heritage"),
    CanonSection("ird", "ИРД — исходно-разрешительная документация", "heritage"),
    CanonSection("pi", "ПИ — предварительные исследования", "heritage"),
    # --- экспертизы ---
    CanonSection("expertise_pd", "Экспертиза ПД (гос./негос.)", "expertise"),
    CanonSection("expertise_sm", "Экспертиза сметной документации", "expertise"),
    # --- прочее ---
    CanonSection("rhr", "РХР — рыбохозяйственный раздел", "other"),
    CanonSection("dop_other", "Дополнительные затраты", "other"),
)

CATALOG_BY_KEY: dict[str, CanonSection] = {s.key: s for s in CATALOG}

# Агрегатная стадия -> доля от суммы design-группы аналога (п. 1.5 СБЦП: ПД 40% / РД 60%).
AGGREGATE_DESIGN_KEYS: dict[str, float] = {
    "pd_total": 0.4,
    "rd_total": 0.6,
    "pd_rd_total": 1.0,
}

# Составные каноны и их части — для анти-задвоения в движке и весов СБЦП.
COMPOSITE_SECTIONS: dict[str, tuple[str, ...]] = {
    "ios2_3": ("ios2", "ios3"),
    "ar_kr": ("ar", "kr"),
}


def normalize_name(raw: str) -> str:
    """Нормализовать название строки: регистр, ё, кавычки, пробелы, хвостовые знаки."""
    text = raw.lower().replace("ё", "е").replace("\xa0", " ")
    text = re.sub(r"[«»\"']", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .:;,-")
    return text


# Точные алиасы (после normalize_name) — короткие аббревиатуры, где keyword-правила опасны.
_ALIASES: dict[str, str] = {
    "см": "smeta",
    "сметы": "smeta",
    "ов": "ios4",
    "ас": "kr",
    "гп": "pzu",
    "по": "po_okn",
    "пи": "pi",
    "эп": "ep",
    "оч": "obmery",
    "ии": "surveys_all",
    "тх": "tx",
    "рп": "project_lead",
    "эн": "ios1",
    "эс": "ios1",
    "нвк": "ios2_3",
    "вк": "ios2",
    "рам": "gap",
    "гап": "gap",
    "км": "kr",
    "эп.ар": "ar",
    "эп.кр": "kr",
    "эп.пз": "pz",
    "пз": "pz",
    "мос здание": "mos",
    "мос.кс": "mos",
    "мос": "mos",
    "пр": "npd",
    "нк": "normokontrol",
    "сту": "stu",
    "ээ": "ee",
    "тс": "ios4",
    "апс": "ios5",
    "апв": "ios5",
    "дпб": "pb",
    "рпб": "pb",
    "дп": "design_project",
    "цим": "bim_manager",
}

# Упорядоченные правила (первое совпадение побеждает). Порядок важен:
# частные случаи раньше общих (ОКНИ до КНИ, шурфы до обследования, ГЭ СМ до СМЕТ).
_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        # управление и накладные
        (r"\bгип\b|сопровождение псд", "gip"),
        (r"руководитель архитектурн", "gap"),
        (r"руководитель проекта", "project_lead"),
        (r"менеджер проекта", "project_manager"),
        (r"администратор проекта|помощник гипа", "project_admin"),
        (r"bim", "bim_manager"),
        (r"нормоконтрол", "normokontrol"),
        (r"проверк\w* с/с|затраты на проверку", "freelance_check"),
        (r"\bрезерв\b", "reserve"),
        (r"содержание офиса", "office"),
        (r"печать документации", "print_docs"),
        (r"ком+андировк", "travel"),
        (r"согласование документации|сопровождение рнс", "approvals"),
        (r"разработка тз", "tz_dev"),
        # экспертизы (до СМЕТ: «ГЭ СМ», «НГЭ СМ»)
        (r"гикэ|историко[- ]культурн\w+ экспертиз", "gike"),
        (r"(?:\bг?гэ\b|\bнгэ\b|экспертиз)\w*.*\bсм\b", "expertise_sm"),
        (r"\bг?гэ\b|\bнгэ\b|экспертиз", "expertise_pd"),
        # ОКН (до изысканий и обследований)
        (r"\bокни\b", "okni"),
        (r"\bкни\b|комплексные научные", "kni"),
        (r"ихти", "ihti"),
        (r"шурф", "shurfy"),
        (r"лабораторн", "labs"),
        (r"поверочн", "poverka"),
        (r"иаби|историко-архивн", "iabi"),
        (r"\bфф\b|фотофиксац", "ff"),
        (r"археолог|историко[- ]культурные изыскания", "archeology"),
        (r"архивная справка", "arch_spravka"),
        (r"обеспечени\w+ сохранност|\bмос\b", "mos"),
        (r"предмет\w? охраны|уточнение по\b|приспособлени\w+ окн", "po_okn"),
        (r"нпд|научно[- ]проектн|предварительные работы", "npd"),
        (r"\bирд\b", "ird"),
        # изыскания и обследования
        (r"игди|геодезич", "igdi"),
        (r"\bиги\b|геолог|геотехнич", "igi"),
        (r"иэи|эколог", "iei"),
        (r"игми|гидромет|метеоролог", "igmi"),
        (r"3d скан|сканирован", "scan3d"),
        (r"обмер", "obmery"),
        (r"\bити\b|\bотс\b|обследова|расчет нагрузок|мониторинг", "iti"),
        (r"изыскан|комплексные ии|\bии\b", "surveys_all"),
        # разделы ПД/РД: частные раньше общих
        (r"тех\w*\.? ?регламент|обращени\w+ с отходами", "waste_reg"),
        (r"иная документация", "other_docs"),
        (r"смет", "smeta"),
        (r"\bсту\b|специальн\w+ технич", "stu"),
        (r"\bээ\b|энергоэффект|энергетическ\w+ эффектив", "ee"),
        (r"ар\+кр", "ar_kr"),
        (r"пзу|схема планировочной|генеральный план|генплан", "pzu"),
        (r"пояснительная записка|\bпз\b", "pz"),
        (r"архитектурно-строительн|конструктивн|конструкци|\bкр\b|\bас\b", "kr"),
        (r"\bар\b|архитектурн\w+ решени|\bархитектор\b|архитектура|паспорт\w? фасад", "ar"),
        (r"иос ?1|электр\w*снабжен|электр\w*освещен|наружное освещение|эом", "ios1"),
        (r"иос ?2.{0,6}иос ?3|вк.{0,3}нвк|водоснабжени\w+ и (?:водоотведени|канализаци)", "ios2_3"),
        (r"иос ?2|водоснабжен", "ios2"),
        (r"иос ?3|водоотведен|канализац|ливнев", "ios3"),
        (r"иос ?4|отоплен|вентиляц|теплов\w+ сет|теплоснабжен", "ios4"),
        (
            r"иос ?5|сет\w+ связи|\bсс\b|слаботочн|видеонаблюден|wi-?fi|свн|соуэ|пб ?2"
            r"|автоматизац|комплексная безопасность",
            "ios5",
        ),
        (r"иос ?6|газоснабжен", "ios6"),
        (r"иос ?7|\bтр\b|\bтх\b|технологич", "tx"),
        (r"проект организации строительства|\bпос\b", "pos"),
        (r"подд", "other_docs"),
        (r"демонтаж|\bпод\b", "pod"),
        (r"\bпор\b", "por"),
        (r"\bоос\b|охране окружающей среды|охран\w+ окружающ", "oos"),
        (r"\bпб ?1?\b|пожарн", "pb"),
        (r"\bоди\b|доступ\w? инвалид|маломобильн", "odi"),
        (r"тбэ|безопасн\w+ эксплуатац", "tbe"),
        (r"гочс", "gochs"),
        (r"\bэп\b|эскизн|концепц|предпроектн", "ep"),
        (r"визуализ|графический дизайнер", "viz"),
        (r"дизайн[ -]?проект|брендинг|дизайнер интерьер", "design_project"),
        (r"ландшафт", "landscape"),
        # агрегаты стадий — В КОНЦЕ design-блока: частные правила выше (печать/иная/
        # согласование документации, ГЭ ПД, разделы) должны побеждать
        (r"проектн\w+ и рабоч\w+ документаци", "pd_rd_total"),
        (r"рабоч\w+ документаци|\bрд\b", "rd_total"),
        (r"проектн\w+ документаци|\bпд\b", "pd_total"),
        # прочее
        (r"\bрхр\b|рыбохозяйствен", "rhr"),
        (r"дополнительны\w+ затрат|корректировк|затраты проектной группы", "dop_other"),
    )
)


# Узкий распознаватель агрегатных стадий — фолбэк движка для строк с canon=None.
# Голые аббревиатуры («пд», «рд») принимаются только как ЦЕЛОЕ название, чтобы не
# зацепить «сопровождение экспертизы ПД» и подобное.
_AGGREGATE_EXACT: dict[str, str] = {
    "пд": "pd_total",
    "рд": "rd_total",
    "пд+рд": "pd_rd_total",
    "пд и рд": "pd_rd_total",
    "стадия п": "pd_total",
    "стадия р": "rd_total",
}
_AGGREGATE_RX: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), key)
    for pattern, key in (
        (r"предпроектн", "ep"),
        (r"проектн\w+ и рабоч\w+ документаци", "pd_rd_total"),
        (r"рабоч\w+ документаци", "rd_total"),
        (r"проектн\w+ документаци", "pd_total"),
    )
)


def detect_aggregate(raw: str) -> str | None:
    """Название строки ТЗ -> агрегатный канон стадии (или ep), если это агрегат."""
    name = normalize_name(raw)
    if not name:
        return None
    exact = _AGGREGATE_EXACT.get(name)
    if exact is not None:
        return exact
    # «Экспертиза/согласование/печать … документации» — работа НАД документацией,
    # а не сама разработка: агрегатом не считаем
    if re.search(r"экспертиз|согласован|печать|провер", name):
        return None
    for pattern, key in _AGGREGATE_RX:
        if pattern.search(name):
            return key
    return None


def match_canon(raw: str) -> str | None:
    """Название строки -> ключ каталога, либо None (не распознано)."""
    name = normalize_name(raw)
    if not name:
        return None
    alias = _ALIASES.get(name)
    if alias is not None:
        return alias
    for pattern, key in _RULES:
        if pattern.search(name):
            return key
    return None
