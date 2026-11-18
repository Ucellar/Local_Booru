"""Validation shared by the independent download queues."""


def reordered(items: list[dict], expected: list[int], order: list[int], *, key: str = "id") -> list[dict] | None:
    """Return the waiting records in the requested order if the caller's locked snapshot still matches."""
    if not isinstance(expected, list) or not isinstance(order, list) or not items:
        return None
    if len(expected) != len(items) or len(order) != len(items):
        return None
    if any(type(number) is not int for number in expected + order):
        return None
    by_id = {item[key]: item for item in items}
    if len(by_id) != len(items) or expected != list(by_id) or set(order) != by_id.keys():
        return None
    return [by_id[number] for number in order]
