from __future__ import annotations

from urllib.parse import quote_plus, urlencode


def apply_target_source_queries(retailers) -> None:
    """Tune B&H and Best Buy discovery to the focused Air target.

    The final target filter still decides what qualifies. These URLs deliberately
    do not cap storage at 1.9 TB, so a 2 TB configuration can alert if it ever
    falls under the price ceiling.
    """

    bh_terms = [
        f"Apple 15 MacBook Air {chip} {memory}GB"
        for chip in ("M4", "M5")
        for memory in (24, 32)
    ]
    retailers.BH_URLS = [
        f"https://www.bhphotovideo.com/c/search?q={quote_plus(term)}&sts=ma"
        for term in bh_terms
    ]

    urls = []
    for memory in (24, 32):
        params = {
            "browsedCategory": "pcmcat247400050001",
            "id": "pcat17071",
            "qp": f"systemmemoryram_facet=RAM~{memory} gigabytes",
            "st": "categoryid$pcmcat247400050001",
            "intl": "nosplash",
        }
        urls.append("https://www.bestbuy.com/site/searchpage.jsp?" + urlencode(params))
    retailers.BESTBUY_URLS = urls
