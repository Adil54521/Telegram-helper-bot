import csv
import io
import re
from collections.abc import Iterable, Sequence

DANGEROUS_PREFIXES = ("=", "+", "-", "@")
LEADING_WHITESPACE_RE = re.compile(r"^[\s\x00-\x1f\x7f]*")


def safe_csv_value(value: object) -> str:
    if value is None:
        return ""

    text = str(value)

    candidate = LEADING_WHITESPACE_RE.sub("", text)

    if candidate.startswith(DANGEROUS_PREFIXES):
        return "'" + text

    return text


def build_csv(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> bytes:
    output = io.StringIO(newline="")

    writer = csv.writer(
        output,
        delimiter=",",
        quoting=csv.QUOTE_MINIMAL,
    )

    writer.writerow(headers)

    for row in rows:
        writer.writerow([
            safe_csv_value(value)
            for value in row
        ])

    return output.getvalue().encode("utf-8-sig")