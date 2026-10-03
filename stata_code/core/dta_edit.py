"""Read and edit the label metadata of a ``.dta`` file without Stata.

Covers what :mod:`stata_code.core.dta_labels` does not: value labels (the
sets ``label define`` creates and the set each variable was given by ``label
values``), the dataset label, and reading notes. Variable labels can ride
along, so one call makes one consistent change to the file.

How a change reaches the file depends on whether it moves anything:

* A variable label, the value-label name attached to a variable, and the
  dataset label of a format 113-115 file are fixed-width fields. They are
  overwritten in place, as ``dta_labels`` does, and nothing else moves.
* The contents of a value-label set and the dataset label of a format 117+
  file are variable-length. The file is then written again to a temporary
  file beside the original (every byte outside the changed fields is copied,
  never re-encoded), the offsets in ``<map>`` are corrected, and the
  temporary file replaces the original in one ``os.replace``. A crash leaves
  either the old file or the new one.

Written against StataCorp's published format documentation (``help dta``),
which LICENSE-POLICY.md §2.1 lists as an allowed reference. This is the
Python counterpart of ``vscode/src/dtaWriter.ts``: both must accept and
refuse the same edits and write the same bytes, which
``vscode/test-fixtures/dta/edit_cases.json`` holds them to.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from stata_code.core.dta_labels import (
    DtaLabelError,
    LabelChange,
    _decode,
)
from stata_code.core.dta_labels import (
    _encode as _encode_variable_label,
)

__all__ = [
    "MAX_VALUE_LABEL_BYTES",
    "DtaEdit",
    "DtaMetadata",
    "edit_labels",
    "read_metadata",
]

#: Stata's limit on the text of one value label, in bytes.
MAX_VALUE_LABEL_BYTES = 32000
#: The integers a value label can be attached to (``help limits``).
_MIN_CODE, _MAX_CODE = -2147483647, 2147483620
#: In a value-label table ``.`` is this integer and ``.a`` ... ``.z`` follow.
_MISSING_BASE = 2147483621
_NEW_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}\Z")
_MISSING_CODE = re.compile(r"\.[a-z]\Z")
_NOTE = re.compile(r"note(\d+)\Z")

Code = int | str


# --------------------------------------------------------------------- model
@dataclass(frozen=True)
class DtaMetadata:
    """The label metadata of one file, as Stata would list it."""

    release: int
    names: list[str]
    #: Stata storage type per variable: ``byte`` ... ``double``, ``str12``, ``strL``.
    types: list[str]
    labels: list[str]
    #: Name of the value label attached to each variable, ``""`` for none.
    value_label_names: list[str]
    #: Value-label sets by name; a code is an ``int`` or ``'.a'`` ... ``'.z'``.
    value_labels: dict[str, dict[Code, str]]
    data_label: str
    #: Notes by variable name; dataset notes are under ``'_dta'``.
    notes: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "release": self.release,
            "data_label": self.data_label,
            "variables": [
                {
                    "name": name,
                    "type": self.types[i],
                    "label": self.labels[i],
                    "value_label": self.value_label_names[i],
                    **({"notes": self.notes[name]} if name in self.notes else {}),
                }
                for i, name in enumerate(self.names)
            ],
            "value_labels": {
                name: {str(code): text for code, text in table.items()}
                for name, table in self.value_labels.items()
            },
            "notes": self.notes.get("_dta", []),
        }


@dataclass(frozen=True)
class DtaEdit:
    """What :func:`edit_labels` changed (or, on a dry run, would change)."""

    variable_labels: list[LabelChange] = field(default_factory=list)
    #: ``{name: 'defined' | 'modified' | 'dropped'}``
    value_labels: dict[str, str] = field(default_factory=dict)
    #: ``before`` / ``after`` are value-label names, ``""`` for none.
    attached: list[LabelChange] = field(default_factory=list)
    data_label: LabelChange | None = None
    #: True when the file was written again in full rather than patched.
    rewritten: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.variable_labels or self.value_labels or self.attached or self.data_label)

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_labels": [c.to_dict() for c in self.variable_labels],
            "value_labels": dict(self.value_labels),
            "attached": [c.to_dict() for c in self.attached],
            "data_label": None if self.data_label is None else self.data_label.to_dict(),
            "rewritten": self.rewritten,
        }


@dataclass(frozen=True)
class _Splice:
    """Replace ``remove`` bytes at ``offset`` with ``data``."""

    offset: int
    remove: int
    data: bytes


@dataclass(frozen=True)
class _Record:
    """One value-label set as it sits in the file, tags included."""

    name: str
    start: int
    end: int
    table: dict[Code, str]


@dataclass
class _Layout:
    release: int
    endian: str
    size: int
    names: list[str]
    types: list[str]
    labels: list[str]
    label_offset: int
    label_width: int
    set_names: list[str]
    set_offset: int
    name_width: int
    data_label: str
    #: offset and length of everything a new dataset label replaces
    data_label_at: tuple[int, int]
    #: file offset of the 14 map entries, and their values (format 117+)
    map_at: int | None
    map: tuple[int, ...] | None
    #: [start, end) of the value-label records
    vl_start: int
    vl_end: int
    records: list[_Record]
    notes: dict[str, list[str]]

    @property
    def tagged(self) -> bool:
        return self.map is not None


# ------------------------------------------------------------------- parsing
def _read(handle: BinaryIO, offset: int, length: int) -> bytes:
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise DtaLabelError("malformed or truncated .dta file")
    return data


def _expect(data: bytes, pos: int, tag: bytes) -> int:
    if data[pos : pos + len(tag)] != tag:
        raise DtaLabelError(f"malformed .dta file: expected {tag.decode('ascii')} at byte {pos}")
    return pos + len(tag)


def _code_of(value: int) -> Code:
    if value >= _MISSING_BASE:
        return "." + ("" if value == _MISSING_BASE else chr(96 + value - _MISSING_BASE))
    return value


def _value_of(code: Code) -> int:
    if isinstance(code, str):
        return _MISSING_BASE + (ord(code[1]) - 96)
    return code


def _parse_table(raw: bytes, endian: str, release: int) -> dict[Code, str]:
    if len(raw) < 8:
        raise DtaLabelError("malformed .dta file: value-label table is cut off")
    n, text_length = struct.unpack_from(endian + "ii", raw, 0)
    if n < 0 or text_length < 0 or 8 + 8 * n + text_length > len(raw):
        raise DtaLabelError("malformed .dta file: value-label table is inconsistent")
    offsets = struct.unpack_from(f"{endian}{n}i", raw, 8)
    values = struct.unpack_from(f"{endian}{n}i", raw, 8 + 4 * n)
    text = raw[8 + 8 * n : 8 + 8 * n + text_length]
    table: dict[Code, str] = {}
    for off, value in zip(offsets, values):
        if 0 <= off < text_length:
            end = text.find(b"\0", off)
            table[_code_of(value)] = _decode(text[off : end if end >= 0 else text_length], release)
    return table


def _notes(chars: list[tuple[str, str, str]]) -> dict[str, list[str]]:
    numbered: dict[str, list[tuple[int, str]]] = {}
    for varname, name, value in chars:
        m = _NOTE.match(name)
        if m and m.group(1) != "0":  # note0 holds the count
            numbered.setdefault(varname, []).append((int(m.group(1)), value))
    return {var: [text for _, text in sorted(items)] for var, items in numbered.items()}


def _modern_type(code: int) -> str:
    if 1 <= code <= 2045:
        return f"str{code}"
    names = {
        32768: "strL",
        65525: "alias",
        65526: "double",
        65527: "float",
        65528: "long",
        65529: "int",
        65530: "byte",
    }
    if code not in names:
        raise DtaLabelError(f"malformed .dta file: unknown variable type code {code}")
    return names[code]


_LEGACY_TYPES = {
    251: ("byte", 1),
    252: ("int", 2),
    253: ("long", 4),
    254: ("float", 4),
    255: ("double", 8),
}


def _fields(raw: bytes, n: int, width: int, release: int) -> list[str]:
    return [_decode(raw[i * width : (i + 1) * width], release) for i in range(n)]


def _locate_tagged(handle: BinaryIO, head: bytes, size: int) -> _Layout:
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
    wide = release in (119, 121)
    (n_vars,) = struct.unpack_from(endian + ("I" if wide else "H"), head, pos)
    pos = _expect(head, pos + (4 if wide else 2), b"</K><N>")
    pos = _expect(head, pos + (4 if release == 117 else 8), b"</N><label>")
    prefix = 1 if release == 117 else 2
    (label_length,) = struct.unpack_from(endian + ("B" if prefix == 1 else "H"), head, pos)
    data_label_at = (pos, prefix + label_length)
    data_label = _decode(head[pos + prefix : pos + prefix + label_length], release)
    pos = _expect(head, pos + prefix + label_length, b"</label><timestamp>")
    pos = _expect(head, pos + 1 + head[pos], b"</timestamp></header><map>")
    map_at = pos
    section = struct.unpack_from(endian + "14Q", head, pos)
    for i in range(2, 13):
        if section[i] < section[i - 1] or section[i] > size:
            raise DtaLabelError("malformed or truncated .dta file: section map is out of range")

    name_width = 33 if release == 117 else 129
    label_width = 81 if release == 117 else 321

    def body(index: int, tag: bytes, length: int) -> tuple[int, bytes]:
        if _read(handle, section[index], len(tag)) != tag:
            raise DtaLabelError(
                f"malformed .dta file: {tag.decode('ascii')} is not where the map says"
            )
        offset = section[index] + len(tag)
        return offset, _read(handle, offset, length)

    _, raw_types = body(2, b"<variable_types>", 2 * n_vars)
    types = [_modern_type(c) for c in struct.unpack(f"{endian}{n_vars}H", raw_types)]
    _, raw_names = body(3, b"<varnames>", n_vars * name_width)
    set_offset, raw_sets = body(6, b"<value_label_names>", n_vars * name_width)
    label_offset, raw_labels = body(7, b"<variable_labels>", n_vars * label_width)

    chars_raw = _read(handle, section[8], section[9] - section[8])
    cpos = _expect(chars_raw, 0, b"<characteristics>")
    chars: list[tuple[str, str, str]] = []
    while chars_raw[cpos : cpos + 4] == b"<ch>":
        (length,) = struct.unpack_from(endian + "I", chars_raw, cpos + 4)
        entry = chars_raw[cpos + 8 : cpos + 8 + length]
        cpos = _expect(chars_raw, cpos + 8 + length, b"</ch>")
        if length >= 2 * name_width:
            name = _decode(entry[name_width : 2 * name_width], release)
            if _NOTE.match(name):
                chars.append(
                    (
                        _decode(entry[:name_width], release),
                        name,
                        _decode(entry[2 * name_width :], release),
                    )
                )

    vl_raw = _read(handle, section[11], section[12] - section[11])
    vpos = _expect(vl_raw, 0, b"<value_labels>")
    vl_start = section[11] + vpos
    records: list[_Record] = []
    while vl_raw[vpos : vpos + 5] == b"<lbl>":
        start = vpos
        (length,) = struct.unpack_from(endian + "I", vl_raw, vpos + 5)
        at = vpos + 9
        name = _decode(vl_raw[at : at + name_width], release)
        table_at = at + name_width + 3
        table = _parse_table(vl_raw[table_at : table_at + length], endian, release)
        vpos = _expect(vl_raw, table_at + length, b"</lbl>")
        records.append(_Record(name, section[11] + start, section[11] + vpos, table))
    _expect(vl_raw, vpos, b"</value_labels>")

    return _Layout(
        release=release,
        endian=endian,
        size=size,
        names=_fields(raw_names, n_vars, name_width, release),
        types=types,
        labels=_fields(raw_labels, n_vars, label_width, release),
        label_offset=label_offset,
        label_width=label_width,
        set_names=_fields(raw_sets, n_vars, name_width, release),
        set_offset=set_offset,
        name_width=name_width,
        data_label=data_label,
        data_label_at=data_label_at,
        map_at=map_at,
        map=section,
        vl_start=vl_start,
        vl_end=section[11] + vpos,
        records=records,
        notes=_notes(chars),
    )


def _locate_legacy(handle: BinaryIO, head: bytes, size: int) -> _Layout:
    release = head[0]
    if head[1] not in (1, 2):
        raise DtaLabelError("malformed .dta file: unknown byte order")
    endian = "<" if head[1] == 2 else ">"
    if len(head) < 109:
        raise DtaLabelError("malformed or truncated .dta file")
    (n_vars,) = struct.unpack_from(endian + "H", head, 4)
    (n_obs,) = struct.unpack_from(endian + "I", head, 6)
    data_label = _decode(head[10:91], release)
    format_width = 12 if release == 113 else 49
    desc_length = n_vars * (1 + 33 + format_width + 33 + 81) + 2 * (n_vars + 1)
    desc = _read(handle, 109, desc_length)
    types: list[str] = []
    row_width = 0
    for code in desc[:n_vars]:
        if 1 <= code <= 244:
            types.append(f"str{code}")
            row_width += code
        elif code in _LEGACY_TYPES:
            types.append(_LEGACY_TYPES[code][0])
            row_width += _LEGACY_TYPES[code][1]
        else:
            raise DtaLabelError(f"malformed .dta file: unknown variable type code {code}")
    at = n_vars
    raw_names = desc[at : at + n_vars * 33]
    at += n_vars * 33 + 2 * (n_vars + 1) + n_vars * format_width
    set_offset = 109 + at
    raw_sets = desc[at : at + n_vars * 33]
    at += n_vars * 33
    label_offset = 109 + at
    raw_labels = desc[at : at + n_vars * 81]

    # expansion fields: (type, length, contents) records ending in 0/0
    chars: list[tuple[str, str, str]] = []
    pos = 109 + desc_length
    while True:
        kind, length = struct.unpack(endian + "BI", _read(handle, pos, 5))
        pos += 5
        if kind == 0 and length == 0:
            break
        if pos + length > size:
            raise DtaLabelError("malformed or truncated .dta file: expansion field overruns EOF")
        if kind == 1 and length >= 66:
            entry = _read(handle, pos, length)
            name = _decode(entry[33:66], release)
            if _NOTE.match(name):
                chars.append((_decode(entry[:33], release), name, _decode(entry[66:], release)))
        pos += length

    data_end = pos + n_obs * row_width
    if data_end > size:
        raise DtaLabelError("malformed or truncated .dta file: data section is cut off")
    tail = _read(handle, data_end, size - data_end)
    records: list[_Record] = []
    tpos = 0
    while tpos < len(tail):
        if tpos + 40 > len(tail):
            raise DtaLabelError("malformed .dta file: value-label section is cut off")
        (length,) = struct.unpack_from(endian + "i", tail, tpos)
        end = tpos + 40 + length
        if length < 8 or end > len(tail):
            raise DtaLabelError("malformed .dta file: value-label section is cut off")
        records.append(
            _Record(
                _decode(tail[tpos + 4 : tpos + 37], release),
                data_end + tpos,
                data_end + end,
                _parse_table(tail[tpos + 40 : end], endian, release),
            )
        )
        tpos = end

    return _Layout(
        release=release,
        endian=endian,
        size=size,
        names=_fields(raw_names, n_vars, 33, release),
        types=types,
        labels=_fields(raw_labels, n_vars, 81, release),
        label_offset=label_offset,
        label_width=81,
        set_names=_fields(raw_sets, n_vars, 33, release),
        set_offset=set_offset,
        name_width=33,
        data_label=data_label,
        data_label_at=(10, 81),
        map_at=None,
        map=None,
        vl_start=data_end,
        vl_end=size,
        records=records,
        notes=_notes(chars),
    )


def _locate(handle: BinaryIO) -> _Layout:
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


def read_metadata(path: str | os.PathLike[str]) -> DtaMetadata:
    """Return the labels, value labels and notes stored in the file at ``path``."""
    with Path(path).open("rb") as handle:
        found = _locate(handle)
    return DtaMetadata(
        release=found.release,
        names=found.names,
        types=found.types,
        labels=found.labels,
        value_label_names=found.set_names,
        value_labels={r.name: dict(r.table) for r in found.records},
        data_label=found.data_label,
        notes=found.notes,
    )


# ------------------------------------------------------------------ encoding
def _check_text(text: str, release: int, what: str) -> bytes:
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise DtaLabelError(f"{what} contains a control character (a line break or a tab?)")
        if 0xD800 <= code <= 0xDFFF:
            raise DtaLabelError(f"{what} contains an unpaired surrogate and is not valid Unicode")
        if release < 118 and code > 0x7E:
            raise DtaLabelError(
                f"this is a format-{release} file (Stata 13 or older), which can "
                "only take plain-ASCII labels here; save it again in Stata 14 or "
                "newer to use other characters"
            )
    return text.encode("utf-8")


def _normalize_code(raw: Any) -> Code:
    if isinstance(raw, bool):
        raise DtaLabelError(f"{raw!r} is not a value-label code")
    if isinstance(raw, str):
        if _MISSING_CODE.match(raw):
            return raw
        if not re.fullmatch(r"-?\d+", raw):
            raise DtaLabelError(f"{raw!r} is not a value-label code (an integer, or .a to .z)")
        raw = int(raw)
    if isinstance(raw, float) and raw == int(raw):
        raw = int(raw)
    if not isinstance(raw, int):
        raise DtaLabelError(f"{raw!r} is not a value-label code (an integer, or .a to .z)")
    if not _MIN_CODE <= raw <= _MAX_CODE:
        raise DtaLabelError(
            f"code {raw} is outside the range a value label can hold ({_MIN_CODE} to {_MAX_CODE})"
        )
    return raw


def _normalize_table(mapping: Any, release: int) -> dict[Code, str]:
    if not isinstance(mapping, Mapping):
        raise DtaLabelError("must be an object mapping codes to label texts, or null")
    if not mapping:
        raise DtaLabelError("has no entries; pass null to drop the value label")
    table: dict[Code, str] = {}
    for raw_code, text in mapping.items():
        code = _normalize_code(raw_code)
        if code in table:
            raise DtaLabelError(f"code {code} is given twice")
        if not isinstance(text, str) or text == "":
            raise DtaLabelError(f"the label of {code} must be a non-empty string")
        encoded = _check_text(text, release, f"the label of {code}")
        if len(encoded) > MAX_VALUE_LABEL_BYTES:
            raise DtaLabelError(
                f"the label of {code} takes {len(encoded)} bytes; Stata allows "
                f"at most {MAX_VALUE_LABEL_BYTES}"
            )
        table[code] = text
    return dict(sorted(table.items(), key=lambda item: _value_of(item[0])))


def _encode_record(name: str, table: dict[Code, str], found: _Layout) -> bytes:
    """One value-label set as the bytes of its record, tags included."""
    offsets: list[int] = []
    text = bytearray()
    for label in table.values():
        offsets.append(len(text))
        text += label.encode("utf-8") + b"\0"
    n = len(table)
    body = (
        struct.pack(found.endian + "ii", n, len(text))
        + struct.pack(f"{found.endian}{n}i", *offsets)
        + struct.pack(f"{found.endian}{n}i", *(_value_of(code) for code in table))
        + bytes(text)
    )
    name_field = name.encode("utf-8").ljust(found.name_width, b"\0") + b"\0\0\0"
    if found.tagged:
        return b"<lbl>" + struct.pack(found.endian + "I", len(body)) + name_field + body + b"</lbl>"
    return struct.pack(found.endian + "i", len(body)) + name_field + body


def _encode_name(name: str, width: int) -> bytes:
    encoded = name.encode("utf-8")
    if len(encoded) > width - 1:
        raise DtaLabelError(
            f"value-label name takes {len(encoded)} bytes; this format has room for {width - 1}"
        )
    return encoded.ljust(width, b"\0")


def _encode_data_label(label: str, found: _Layout) -> bytes:
    # same rules as a variable label: 80 characters, no control characters
    padded = _encode_variable_label(label, found.release, 81 if not found.tagged else 321)
    if not found.tagged:
        return padded
    encoded = label.encode("utf-8")
    if found.release == 117:
        if len(encoded) > 80:
            raise DtaLabelError(
                f"label takes {len(encoded)} bytes; this file format has room for 80"
            )
        return struct.pack("B", len(encoded)) + encoded
    return struct.pack(found.endian + "H", len(encoded)) + encoded


# ------------------------------------------------------------------- editing
def _plan(
    found: _Layout,
    variable_labels: Mapping[str, Any] | None,
    value_labels: Mapping[str, Any] | None,
    attach: Mapping[str, Any] | None,
    data_label: str | None,
) -> tuple[list[_Splice], DtaEdit]:
    index = {name: i for i, name in enumerate(found.names)}
    problems: list[str] = []
    splices: list[_Splice] = []
    label_changes: list[LabelChange] = []
    set_changes: dict[str, str] = {}
    attach_changes: list[LabelChange] = []
    data_change: LabelChange | None = None

    for name, label in (variable_labels or {}).items():
        i = index.get(name)
        if i is None:
            problems.append(f"{name}: no such variable")
        elif not isinstance(label, str):
            problems.append(f"{name}: label must be a string")
        elif label != found.labels[i]:
            try:
                data = _encode_variable_label(label, found.release, found.label_width)
            except DtaLabelError as exc:
                problems.append(f"{name}: {exc}")
                continue
            splices.append(
                _Splice(found.label_offset + i * found.label_width, found.label_width, data)
            )
            label_changes.append(LabelChange(name, found.labels[i], label))

    # value-label sets: the mapping given for a name replaces that set
    existing = {r.name: r for r in found.records}
    replaced: dict[str, dict[Code, str] | None] = {}
    for name, mapping in (value_labels or {}).items():
        if not isinstance(name, str) or not name:
            problems.append("value label names must be non-empty strings")
            continue
        if mapping is None:
            # dropping a set that is not there is already done, not an error
            if name in existing:
                replaced[name] = None
                set_changes[name] = "dropped"
            continue
        if name not in existing and not _NEW_NAME.match(name):
            problems.append(
                f"value label {name}: a name is 1-32 letters, digits or _, "
                "not starting with a digit"
            )
            continue
        try:
            table = _normalize_table(mapping, found.release)
        except DtaLabelError as exc:
            problems.append(f"value label {name}: {exc}")
            continue
        if name in existing and existing[name].table == table:
            continue
        replaced[name] = table
        set_changes[name] = "modified" if name in existing else "defined"

    after_names = {n for n in existing if replaced.get(n, 0) is not None} | {
        n for n, table in replaced.items() if table is not None
    }

    # attachments; a dropped set is detached from the variables that used it
    wanted: dict[str, str] = {}
    for i, current in enumerate(found.set_names):
        if current and replaced.get(current, 0) is None and current in replaced:
            wanted[found.names[i]] = ""
    for name, set_name in (attach or {}).items():
        i = index.get(name)
        if i is None:
            problems.append(f"{name}: no such variable")
        elif not isinstance(set_name, str):
            problems.append(f'{name}: value label name must be a string ("" detaches)')
        elif set_name and found.types[i].startswith("str"):
            problems.append(f"{name}: is a string variable; value labels attach to numbers")
        elif set_name and set_name not in after_names:
            problems.append(f"{name}: no value label named {set_name}; define it in the same call")
        else:
            wanted[name] = set_name
    for name, set_name in wanted.items():
        i = index[name]
        if set_name == found.set_names[i]:
            continue
        try:
            data = _encode_name(set_name, found.name_width)
        except DtaLabelError as exc:
            problems.append(f"{name}: {exc}")
            continue
        splices.append(_Splice(found.set_offset + i * found.name_width, found.name_width, data))
        attach_changes.append(LabelChange(name, found.set_names[i], set_name))

    if data_label is not None:
        if not isinstance(data_label, str):
            problems.append("data_label must be a string")
        elif data_label != found.data_label:
            try:
                data = _encode_data_label(data_label, found)
            except DtaLabelError as exc:
                problems.append(f"data label: {exc}")
            else:
                splices.append(_Splice(found.data_label_at[0], found.data_label_at[1], data))
                data_change = LabelChange("_dta", found.data_label, data_label)

    if problems:
        raise DtaLabelError("; ".join(problems))

    if replaced:
        # untouched sets keep their bytes; changed ones are encoded afresh and
        # stay where they were; new ones go to the end
        pieces: list[tuple[int, int, bytes]] = []
        for record in found.records:
            if record.name in replaced:
                new_table = replaced[record.name]
                data = b"" if new_table is None else _encode_record(record.name, new_table, found)
                pieces.append((record.start, record.end - record.start, data))
        new = b"".join(
            _encode_record(name, table, found)
            for name, table in replaced.items()
            if table is not None and name not in existing
        )
        for start, remove, data in pieces:
            splices.append(_Splice(start, remove, data))
        if new:
            splices.append(_Splice(found.vl_end, 0, new))

    splices.sort(key=lambda s: (s.offset, s.remove))
    if found.map is not None and found.map_at is not None:
        if any(s.remove != len(s.data) for s in splices):
            moved = []
            for entry in found.map:
                shift = sum(len(s.data) - s.remove for s in splices if s.offset < entry)
                moved.append(entry + shift)
            splices.append(_Splice(found.map_at, 14 * 8, struct.pack(found.endian + "14Q", *moved)))
            splices.sort(key=lambda s: (s.offset, s.remove))

    rewritten = any(s.remove != len(s.data) for s in splices)
    return splices, DtaEdit(
        variable_labels=label_changes,
        value_labels=set_changes,
        attached=attach_changes,
        data_label=data_change,
        rewritten=rewritten,
    )


def _rewrite(path: Path, source: BinaryIO, splices: list[_Splice], size: int) -> None:
    """Write the spliced file beside ``path``, then put it in ``path``'s place."""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            pos = 0
            for splice in splices:
                _copy(source, out, pos, splice.offset - pos)
                out.write(splice.data)
                pos = splice.offset + splice.remove
            _copy(source, out, pos, size - pos)
            out.flush()
            os.fsync(out.fileno())
        shutil.copymode(path, temp_name)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _copy(source: BinaryIO, out: BinaryIO, offset: int, length: int) -> None:
    if length < 0:
        raise DtaLabelError("internal error: overlapping edits")
    source.seek(offset)
    remaining = length
    while remaining:
        chunk = source.read(min(remaining, 1 << 20))
        if not chunk:
            raise DtaLabelError("malformed or truncated .dta file")
        out.write(chunk)
        remaining -= len(chunk)


def edit_labels(
    path: str | os.PathLike[str],
    *,
    variable_labels: Mapping[str, str] | None = None,
    value_labels: Mapping[str, Mapping[Any, str] | None] | None = None,
    attach: Mapping[str, str] | None = None,
    data_label: str | None = None,
    dry_run: bool = False,
) -> DtaEdit:
    """Edit the label metadata of the ``.dta`` file at ``path``.

    ``variable_labels``
        ``{variable: label}``; ``""`` removes a label.
    ``value_labels``
        ``{name: {code: text}}`` defines the value label ``name``, replacing
        the whole set if it exists (``label define name ..., replace``);
        ``{name: None}`` drops it and detaches it from the variables that
        used it (a name that is not defined is left alone). A code is an integer or ``".a"`` ... ``".z"``; integers may
        be given as strings, as JSON object keys are.
    ``attach``
        ``{variable: name}`` attaches a value label (``label values``);
        ``""`` detaches. The name must exist once the call's ``value_labels``
        are applied.
    ``data_label``
        The dataset label (``label data``); ``""`` removes it.

    Every edit is validated before any byte is written, so a bad entry leaves
    the file untouched. Returns what changed; an entry that already holds the
    requested value is not a change. With ``dry_run`` the same validation
    runs and the same result comes back, but nothing is written.

    Raises :class:`DtaLabelError` for an unsupported file or an edit Stata
    could not hold.
    """
    target = Path(path)
    with target.open("rb") as handle:
        found = _locate(handle)
        splices, result = _plan(found, variable_labels, value_labels, attach, data_label)
        if dry_run or not splices:
            return result
        if result.rewritten:
            _rewrite(target, handle, splices, found.size)
            return result
    with target.open("r+b") as handle:
        for splice in splices:
            handle.seek(splice.offset)
            handle.write(splice.data)
        handle.flush()
        os.fsync(handle.fileno())
    return result
