from quantdesk.core.types import BookLevel


def levels(value: object) -> tuple[BookLevel, ...]:
    if not isinstance(value, list):
        raise ValueError("levels must be an array")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("malformed level")
        price, size = item.get("price_ticks"), item.get("size_lots")
        # Canonical JSON uses strings for integers outside the JS exact range.
        if isinstance(price, str) and price.isdecimal():
            price = int(price)
        if isinstance(size, str) and size.isdecimal():
            size = int(size)
        if type(price) is not int or type(size) is not int:
            raise ValueError("integer ticks/lots required")
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
