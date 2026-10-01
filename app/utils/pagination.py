from dataclasses import dataclass
from math import ceil
from typing import Generic, Sequence, TypeVar

T = TypeVar("T")
PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class PageSlice(Generic[T]):
    items: list[T]
    page: int
    pages: int
    total: int


def paginate(items: Sequence[T], page: int, page_size: int = PAGE_SIZE) -> PageSlice[T]:
    total = len(items)
    pages = max(1, ceil(total / page_size))
    current = max(0, min(page, pages - 1))
    start = current * page_size
    return PageSlice(list(items[start:start + page_size]), current, pages, total)
