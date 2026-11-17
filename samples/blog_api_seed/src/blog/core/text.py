"""高风险共享工具（对应保底策略的高风险目录）。"""


def slugify(text: str) -> str:
    return "-".join(part for part in text.lower().split() if part)


def excerpt_words(text: str, limit: int = 8) -> str:
    return " ".join(text.split()[:limit])