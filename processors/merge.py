from typing import List

from models.schema import Deal


def merge(deals: List[Deal]) -> List[Deal]:
    by = {}
    for d in deals:
        k = d.project_name.strip()
        if k not in by:
            by[k] = d
        else:
            b = by[k]
            b.sources = list(set(b.sources + d.sources))
            if len(d.detail) > len(b.detail):
                b.detail = d.detail
            if len(d.title) > len(b.title):
                b.title = d.title
            for f in ("amount", "valuation", "investors", "team", "business", "official_site", "founded_year", "verified_date"):
                if getattr(b, f) in ("", "未披露", None) and getattr(d, f) not in ("", "未披露", None):
                    setattr(b, f, getattr(d, f))
            if b.region_class == "未知" and d.region_class != "未知":
                b.region_class = d.region_class
    return list(by.values())
