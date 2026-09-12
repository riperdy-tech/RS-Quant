from quantdesk.core.types import BookLevel


def levels(value: object) -> tuple[BookLevel, ...]:
    from quantdesk.data.canonical import canonical_int

    if not isinstance(value, list):
        raise ValueError("levels must be an array")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"price_ticks", "size_lots"}:
            raise ValueError("malformed level")
        price = canonical_int(item["price_ticks"], minimum=1)
        size = canonical_int(item["size_lots"], minimum=0)
        result.append(BookLevel(price, size))
    if len({level.price_ticks for level in result}) != len(result):
        raise ValueError("duplicate price level")
    return tuple(result)


def validate_book(bids: dict[int, int], asks: dict[int, int], max_levels: int) -> None:
    if not bids or not asks:
        raise ValueError("EMPTY_SIDE")
    if len(bids) + len(asks) > max_levels:
        raise ValueError("BOOK_OVERFLOW")
    if max(bids) >= min(asks):
        raise ValueError("CROSSED_BOOK")
