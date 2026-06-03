import re
from datetime import datetime


def parse_decimal(raw_value: str) -> float:
    cleaned = raw_value.replace("\xa0", "").replace(" ", "").replace(",", ".")
    return float(cleaned)


def parse_date_from_text(text: str):
    patterns = [
        r"\b(\d{2}/\d{2}/\d{4})\b",
        r"\b(\d{2}-\d{2}-\d{4})\b",
        r"\b(\d{2}/\d{2}/\d{2})\b",
        r"\b(\d{2}-\d{2}-\d{2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        value = match.group(1)
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

    return datetime.utcnow().date()
