"""Adapt qvf's public validator; format rules live exclusively in qvf."""

from __future__ import annotations

import importlib
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any, cast

import numpy as np

from vibeview.install_hints import install_hint
from vibeview.qvf import (
    QVFError,
    QVFOpenError,
    QVFReader,
    QVFSource,
    ValidationUnavailableError,
)


def validate_archive(
    source: QVFSource, *, limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use qvf's rules and limits without taking ownership of the input."""
    try:
        reference = importlib.import_module("qvf_reader")
    except ImportError as exc:
        # Not a verdict on the archive: the validator is absent (#108).
        raise ValidationUnavailableError(
            "Strict validation requires qvf-writer>=0.4. Install the Python package "
            "from the qvf release named by QVF_TAG "
            "(python -m pip install '<path-to-qvf>/python[validate]'), then: "
            + install_hint("validation")
        ) from exc

    policy = reference.Limits.from_dict(limits)
    if isinstance(source, (bytes, bytearray, memoryview)):
        with io.BytesIO(bytes(source)) as stream:
            return _validate(reference, stream, policy)
    if isinstance(source, (str, Path)):
        # Use one handle, including for diagnostics, even if the path is replaced.
        try:
            with open(source, "rb") as handle:
                return _validate(reference, handle, policy)
        except OSError:
            return _report(reference.validate_qvf(str(source), limits=policy))
    position = source.tell()
    try:
        return _validate(reference, source, policy)
    finally:
        source.seek(position)


def _report(raw: Any) -> dict[str, Any]:
    result = dict(cast(dict[str, Any], raw))
    result["valid"] = result.pop("ok")
    if not result["valid"]:
        result["error"] = "\n".join(result["errors"])
        # Preserve the SDK's open-error contract without reading refused data
        # again. The original findings remain available to report consumers.
        if any(
            finding["severity"] == "error" and (
                finding["code"] in {"E_MANIFEST_MISSING", "E_MANIFEST_JSON"}
                or finding["code"] == "E_ZIP_UNREADABLE"
                and finding["where"] in {"archive", "entry:manifest.json"}
            )
            for finding in result["findings"]
        ):
            raise QVFOpenError(result["error"], validation_report=result)
    return result


def _validate(reference: Any, stream: Any, policy: Any) -> dict[str, Any]:
    result = _report(reference.validate_qvf(stream, limits=policy))
    if result["valid"]:
        # Preserve the viewer's existing readability contract, after reference
        # preflight. A consumer limitation is not a new QVF format rule/code.
        try:
            with QVFReader(stream) as reader:
                result["n_members"] = sum(len(s.members) for s in reader.sections)
        except QVFError as exc:
            result.update(valid=False, error=str(exc), reader_error=str(exc))
        return result
    errors = [f for f in result["findings"] if f["severity"] == "error"]
    # Diagnostics only: qvf has already checked the container and manifest.
    # Never reopen/decompress an archive rejected by resource or schema checks.
    member_codes = {"E_BINARY_SIZE": "layout", "E_SHA256_MISMATCH": "sha256"}
    if errors and all(f["code"] in member_codes for f in errors):
        with zipfile.ZipFile(stream) as archive:
            if archive.getinfo("manifest.json").file_size > policy.max_json_bytes:
                return result
            manifest = json.loads(archive.read("manifest.json"))
            member_sizes = {info.filename: info.file_size for info in archive.infolist()}
        sections = manifest["sections"]
        first = errors[0]
        parts = first["where"].split("/")
        if len(parts) == 5 and parts[1] == "sections" and parts[3] == "members":
            result.update(
                section=sections[int(parts[2])]["id"],
                member=parts[4].replace("~1", "/").replace("~0", "~"),
                reason=member_codes[first["code"]],
            )
            if first["code"] == "E_BINARY_SIZE":
                member = sections[int(parts[2])]["members"][result["member"]]
                if member.get("format") == "binary" and "dtype" in member and "shape" in member:
                    expected = np.dtype(member["dtype"]).itemsize * math.prod(member["shape"])
                    actual = member_sizes[member["path"]]
                    result["error"] = (
                        f"{result['error']} (declared layout needs {expected:,} bytes; "
                        f"the member holds {actual:,})"
                    )
    return result
