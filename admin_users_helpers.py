from __future__ import annotations

from typing import Iterable

from access_control import normalize_access_value

ROLE_LABELS = {
    "admin": "مدير النظام",
    "partner": "شريك",
    "employee": "موظف",
    "project_manager": "مدير مشروع",
    "owner": "مالك",
    "tenant": "مستأجر",
}

COMPANY_LABELS = {
    "works": "المقاولات",
    "realestate": "التطوير العقاري / إدارة الأملاك",
    "logistics": "اللوجستيات",
    "general": "الإدارة العامة",
    "all": "جميع الشركات",
}

COMPANY_PAGE_CONFIG = {
    "works": {
        "label": COMPANY_LABELS["works"],
        "title": "صفحة مستخدمي المقاولات",
        "description": "إدارة موظفي المقاولات فقط مع فصل واضح بين موظف السجل اليومي وموظف المصروفات.",
        "roles": ["employee"],
        "sections": [
            ("daily_log", "السجل اليومي"),
            ("expenses", "المصروفات"),
        ],
    },
    "realestate": {
        "label": COMPANY_LABELS["realestate"],
        "title": "صفحة مستخدمي التطوير العقاري / إدارة الأملاك",
        "description": "إدارة مستخدمي العقار مع المحافظة على ربط المالك والمستأجر كما هو.",
        "roles": ["employee", "project_manager", "owner", "tenant"],
        "sections": [
            ("property_accounts", "موظف إدارة الأملاك / الحسابات"),
            ("maintenance", "موظف الصيانة"),
            ("active_projects", "مدير المشاريع النشطة"),
        ],
    },
    "logistics": {
        "label": COMPANY_LABELS["logistics"],
        "title": "صفحة مستخدمي اللوجستيات",
        "description": "إدارة مستخدمي اللوجستيات مع إظهار الأقسام الخاصة بالعمليات اللوجستية فقط.",
        "roles": ["employee", "partner"],
        "sections": [
            ("inventory", "المستودع"),
        ],
    },
    "general": {
        "label": COMPANY_LABELS["general"],
        "title": "صفحة الإدارة العامة",
        "description": "إدارة مدير النظام والشركاء ومستخدمي المستودع من مكان واحد وبواجهة عربية واضحة.",
        "roles": ["admin", "partner", "employee"],
        "sections": [
            ("inventory", "المستودع"),
        ],
    },
}

GENERAL_PARTNER_COMPANY_OPTIONS = [
    ("works", COMPANY_LABELS["works"]),
    ("realestate", COMPANY_LABELS["realestate"]),
    ("logistics", COMPANY_LABELS["logistics"]),
]


def get_role_label(role: str) -> str:
    clean_role = normalize_access_value(role)
    return ROLE_LABELS.get(clean_role, clean_role or "-")


def get_company_label(company: str) -> str:
    clean_company = normalize_access_value(company)
    return COMPANY_LABELS.get(clean_company, clean_company or "-")


def get_company_page_config(company: str) -> dict:
    clean_company = normalize_access_value(company)
    return COMPANY_PAGE_CONFIG.get(clean_company, COMPANY_PAGE_CONFIG["general"])


def get_allowed_sections_for_company(company: str) -> list[tuple[str, str]]:
    config = get_company_page_config(company)
    return list(config.get("sections", []))


def get_section_label(company: str, section: str) -> str:
    clean_section = normalize_access_value(section)
    if not clean_section:
        return "-"

    for value, label in get_allowed_sections_for_company(company):
        if value == clean_section:
            return label

    fallback_labels = {
        "daily_log": "السجل اليومي",
        "expenses": "المصروفات",
        "inventory": "المستودع",
        "property_accounts": "موظف إدارة الأملاك / الحسابات",
        "maintenance": "موظف الصيانة",
        "active_projects": "مدير المشاريع النشطة",
    }
    return fallback_labels.get(clean_section, clean_section)


def get_allowed_roles_for_company(company: str) -> list[str]:
    config = get_company_page_config(company)
    return list(config.get("roles", []))


def get_general_partner_company_options() -> list[tuple[str, str]]:
    return list(GENERAL_PARTNER_COMPANY_OPTIONS)


def user_matches_company_scope(user: dict, access_rows: Iterable[dict], company: str) -> bool:
    clean_company = normalize_access_value(company)
    clean_role = normalize_access_value(user["role"] or "")
    access_rows = list(access_rows)

    if clean_company == "general":
        if clean_role in {"admin", "partner"}:
            return True
        if clean_role != "employee":
            return False
        return any(normalize_access_value(row["section"] or "") == "inventory" for row in access_rows)

    if clean_company == "realestate":
        if clean_role in {"owner", "tenant"}:
            return True
        if clean_role not in {"employee", "project_manager"}:
            return False
        return any(normalize_access_value(row["company"] or "") == "realestate" for row in access_rows)

    if clean_role not in {"employee", "partner"}:
        return False

    for row in access_rows:
        row_company = normalize_access_value(row["company"] or "")
        if row_company in {clean_company, "all"}:
            return True
    return False
