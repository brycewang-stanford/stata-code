"""Read and edit variable labels in a ``.dta`` file without Stata.

A variable label is a fixed-width, zero-terminated field in every ``.dta``
format since Stata 8 (81 bytes through format 117, 321 bytes from 118 on; see
``help dta``). Replacing one overwrites that field and nothing else: no offset
moves, and the data, value labels, notes, characteristics and strLs keep their
bytes. That is why :func:`set_variable_labels` patches the file in place
rather than loading and re-saving it, which would need Stata (or would drop
whatever the re-saving library does not model).

Written against StataCorp's published format documentation, which
LICENSE-POLICY.md §2.1 lists as an allowed reference. This is the Python
counterpart of ``vscode/src/dtaWriter.ts`` and must accept and refuse the same
labels; both are tested on the same Stata-written fixtures.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "MAX_VARIABLE_LABEL_CHARS",
    "DtaLabelError",
    "DtaLabels",
    "LabelChange",
    "read_variable_labels",
    "set_variable_labels",
]

#: Stata's limit on a variable label, in characters.
MAX_VARIABLE_LABEL_CHARS = 80

_VARIABLE_LABELS_TAG = b"<variable_labels>"
_VARNAMES_TAG = b"<varnames>"


class DtaLabelError(ValueError):
    """The file is not a supported ``.dta`` file, or a label cannot be stored."""


@dataclass(frozen=True)
class LabelChange:
    name: str
    before: str
    after: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class DtaLabels:
    """Where the variable labels of one file live, and what they say."""

    release: int
    names: list[str]
    labels: list[str]
    #: File offset of the first label field.
    offset: int
    #: Width of each label field in bytes.
    width: int

    def as_dict(self) -> dict[str, str]:
        return dict(zip(self.names, self.labels))


def _zero_terminated(raw: bytes) -> bytes:
    end = raw.find(b"\0")
    return raw if end < 0 else raw[:end]


def _decode(raw: bytes, release: int) -> str:
    raw = _zero_terminated(raw)
    if release >= 118:
        return raw.decode("utf-8", errors="replace")
    # Older formats do not record their encoding; mirror the viewer's "auto".
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1252", errors="replace")


def _read_exact(handle: BinaryIO, offset: int, length: int) -> bytes:
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise DtaLabelError("malformed or truncated .dta file")
    return data


def _expect(data: bytes, pos: int, tag: bytes) -> int:
    if data[pos : pos + len(tag)] != tag:
        raise DtaLabelError(
            f"malformed .dta file: expected {tag.decode('ascii')} at byte {pos}"
        )
    return pos + len(tag)


def _locate_tagged(handle: BinaryIO, head: bytes, size: int) -> DtaLabels:
    pos = _expect(head, 0, b"<stata_dta><header><release>")
    try:
        release = int(head[pos : pos + 3].decode("ascii"))
    except ValueError:
        raise DtaLabelError("malformed .dta file: unreadable release number") from None
    if release not in (117, 118, 119, 120, 121):
        raise DtaLabelError(f".dta format {release} is not supported")
    pos = _expect(head, pos + 3, b"</release><byteorder>")
    order = head[pos : pos + 3]
    if order not in (b"LSF", b"MSF"):
        raise DtaLabelError("malformed .dta file: unknown byte order")
    endian = "<" if order == b"LSF" else ">"
    pos = _expect(head, pos + 3, b"</byteorder><K>")
    wide = release in (119, 121)  # more than 32,767 variables
    if wide:
        (n_vars,) = struct.unpack_from(endian + "I", head, pos)
        pos += 4
    else:
        (n_vars,) = struct.unpack_from(endian + "H", head, pos)
        pos += 2
    pos = _expect(head, pos, b"</K><N>")
    pos += 4 if release == 117 else 8
    pos = _expect(head, pos, b"</N><label>")
    if release == 117:
        label_length = head[pos]
        pos += 1
    else:
        (label_length,) = struct.unpack_from(endian + "H", head, pos)
        pos += 2
    pos = _expect(head, pos + label_length, b"</label><timestamp>")
    pos = _expect(head, pos + 1 + head[pos], b"</timestamp></header><map>")
    section = struct.unpack_from(endian + "14Q", head, pos)

    name_width = 33 if release == 117 else 129
    label_width = 81 if release == 117 else 321
    names_at, labels_at = section[3], section[7]
    names_bytes = n_vars * name_width
    labels_bytes = n_vars * label_width
    if (
        names_at + len(_VARNAMES_TAG) + names_bytes > size
        or labels_at + len(_VARIABLE_LABELS_TAG) + labels_bytes > size
    ):
        raise DtaLabelError("malformed or truncated .dta file: section map is out of range")

    if _read_exact(handle, names_at, len(_VARNAMES_TAG)) != _VARNAMES_TAG:
        raise DtaLabelError("malformed .dta file: <varnames> is not where the map says")
    raw_names = _read_exact(handle, names_at + len(_VARNAMES_TAG), names_bytes)
    if _read_exact(handle, labels_at, len(_VARIABLE_LABELS_TAG)) != _VARIABLE_LABELS_TAG:
        raise DtaLabelError(
            "malformed .dta file: <variable_labels> is not where the map says"
        )
    offset = labels_at + len(_VARIABLE_LABELS_TAG)
    raw_labels = _read_exact(handle, offset, labels_bytes)
    return DtaLabels(
        release=release,
        names=[
            _decode(raw_names[i * name_width : (i + 1) * name_width], release)
            for i in range(n_vars)
        ],
        labels=[
            _decode(raw_labels[i * label_width : (i + 1) * label_width], release)
            for i in range(n_vars)
        ],
        offset=offset,
        width=label_width,
    )


def _locate_legacy(handle: BinaryIO, head: bytes, size: int) -> DtaLabels:
    release = head[0]
    if head[1] not in (1, 2):
        raise DtaLabelError("malformed .dta file: unknown byte order")
    endian = "<" if head[1] == 2 else ">"
    (n_vars,) = struct.unpack_from(endian + "H", head, 4)
    format_width = 12 if release == 113 else 49
    names_at = 109 + n_vars
    offset = names_at + n_vars * 33 + 2 * (n_vars + 1) + n_vars * (format_width + 33)
    if offset + n_vars * 81 > size:
        raise DtaLabelError("malformed or truncated .dta file")
    raw_names = _read_exact(handle, names_at, n_vars * 33)
    raw_labels = _read_exact(handle, offset, n_vars * 81)
    return DtaLabels(
        release=release,
        names=[_decode(raw_names[i * 33 : (i + 1) * 33], release) for i in range(n_vars)],
        labels=[_decode(raw_labels[i * 81 : (i + 1) * 81], release) for i in range(n_vars)],
        offset=offset,
        width=81,
    )


def _locate(handle: BinaryIO) -> DtaLabels:
    size = os.fstat(handle.fileno()).st_size
    handle.seek(0)
    head = handle.read(4096)
    if len(head) < 4:
        raise DtaLabelError("not a Stata .dta file (file is too short)")
    try:
        if head[:1] == b"<":
            return _locate_tagged(handle, head, size)
        if 113 <= head[0] <= 115:
            return _locate_legacy(handle, head, size)
    except (struct.error, IndexError):
        raise DtaLabelError("malformed or truncated .dta file") from None
    if 102 <= head[0] <= 112:
        raise DtaLabelError(
            f".dta format {head[0]} (Stata 7 or older) is not supported; "
            "open it in Stata and save it again to convert it"
        )
    raise DtaLabelError("not a Stata .dta file (unrecognized header)")


def read_variable_labels(path: str | os.PathLike[str]) -> DtaLabels:
    """Return the variable names and labels stored in the ``.dta`` file at ``path``."""
    with Path(path).open("rb") as handle:
        return _locate(handle)


def _encode(label: str, release: int, width: int) -> bytes:
    if len(label) > MAX_VARIABLE_LABEL_CHARS:
        raise DtaLabelError(
            f"label is {len(label)} characters; Stata allows at most "
            f"{MAX_VARIABLE_LABEL_CHARS}"
        )
    for ch in label:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise DtaLabelError(
                "label contains a control character (a line break or a tab?)"
            )
        if 0xD800 <= code <= 0xDFFF:
            raise DtaLabelError(
                "label contains an unpaired surrogate and is not valid Unicode"
            )
        if release < 118 and code > 0x7E:
            # These formats predate UTF-8 and do not record which encoding they
            # use, so no non-ASCII label would read the same everywhere.
            raise DtaLabelError(
                f"this is a format-{release} file (Stata 13 or older), which can "
                "only take plain-ASCII labels here; save it again in Stata 14 or "
                "newer to use other characters"
            )
    encoded = label.encode("utf-8")
    if len(encoded) > width - 1:
        raise DtaLabelError(
            f"label takes {len(encoded)} bytes; this file format has room for {width - 1}"
        )
    return encoded.ljust(width, b"\0")


def set_variable_labels(
    path: str | os.PathLike[str],
    labels: dict[str, str],
    *,
    dry_run: bool = False,
) -> list[LabelChange]:
    """Set variable labels in the ``.dta`` file at ``path``, in place.

    ``labels`` maps variable name to the new label; ``""`` removes a label.
    Every edit is validated before any byte is written, so a bad entry leaves
    the file untouched. Returns the labels that actually changed (an entry that
    already holds the requested text is not a change). With ``dry_run`` the
    same validation runs and the same list comes back, but nothing is written.

    Raises :class:`DtaLabelError` for an unsupported file, an unknown variable,
    or a label Stata could not hold.
    """
    with Path(path).open("rb" if dry_run else "r+b") as handle:
        found = _locate(handle)
        index = {name: i for i, name in enumerate(found.names)}
        patches: list[tuple[int, bytes, LabelChange]] = []
        problems: list[str] = []
        for name, label in labels.items():
            i = index.get(name)
            if i is None:
                problems.append(f"{name}: no such variable")
                continue
            if not isinstance(label, str):
                problems.append(f"{name}: label must be a string")
                continue
            if label == found.labels[i]:
                continue
            try:
                field = _encode(label, found.release, found.width)
            except DtaLabelError as exc:
                problems.append(f"{name}: {exc}")
                continue
            patches.append(
                (found.offset + i * found.width, field, LabelChange(name, found.labels[i], label))
            )
        if problems:
            raise DtaLabelError("; ".join(problems))
        if not dry_run and patches:
            for offset, field, _ in patches:
                handle.seek(offset)
                handle.write(field)
            handle.flush()
            os.fsync(handle.fileno())
        return [change for _, _, change in patches]
