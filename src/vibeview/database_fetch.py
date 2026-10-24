"""Explicit-only public structure retrieval. No startup, guessing or local uploads."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

MAX_BYTES = 32 * 1024 * 1024


def _download(url: str) -> bytes:
    try:
        with urlopen(
            Request(url, headers={"User-Agent": "vibe-view structure fetch"}), timeout=20
        ) as response:
            data = bytes(response.read(MAX_BYTES + 1))
        if len(data) > MAX_BYTES:
            raise ValueError("Structure response exceeds 32 MiB; download and open locally")
        return data
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ValueError(
            "Database unavailable or identifier not found. Check network/offline status and"
            " identifier; local files remain available."
        ) from exc


def _fetch_structure(
    provider: str, identifier: str, *, transport: Callable[[str], bytes] = _download
) -> io.BytesIO:
    """Fetch exactly the identifier supplied by an explicit user action."""
    from vibeview.biomolecule import mmcif_models
    from vibeview.converters import sdf_to_qvf

    value = identifier.strip()
    if provider == "RCSB":
        if not re.fullmatch(r"[0-9][A-Za-z0-9]{3}", value):
            raise ValueError("Enter a four-character PDB identifier, e.g. 1CRN")
        return mmcif_models(
            transport(f"https://files.rcsb.org/download/{value.upper()}.cif").decode()
        )
    if provider == "AlphaFold":
        if not re.fullmatch(r"[A-Za-z0-9]{6,10}(?:-\d+)?", value):
            raise ValueError("Enter a UniProt accession, e.g. P69905")
        metadata = json.loads(
            transport(f"https://alphafold.ebi.ac.uk/api/prediction/{value.upper()}")
        )
        if not isinstance(metadata, list) or len(metadata) != 1:
            raise ValueError(
                "Expected one AlphaFold prediction; use a specific accession or download the "
                "desired model locally"
            )
        url = metadata[0].get("cifUrl", "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "alphafold.ebi.ac.uk"
            or not parsed.path.startswith("/files/")
        ):
            raise ValueError("AlphaFold response did not contain an approved structure URL")
        return mmcif_models(transport(url).decode())
    if provider in ("PubChem CID", "PubChem name"):
        if provider == "PubChem CID":
            if not re.fullmatch(r"[1-9]\d{0,11}", value):
                raise ValueError("Enter a positive PubChem CID")
            kind = "cid"
        else:
            if (
                not value
                or len(value) > 120
                or any(ord(c) < 32 for c in value)
                or "://" in value
                or "@" in value
                or value.startswith(("/", "~", "\\"))
            ):
                raise ValueError(
                    "Enter a public chemical name, not a URL, path or personal identifier"
                )
            kind = "name"
        url = (
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/{kind}/"
            f"{quote(value, safe='')}/SDF?record_type=3d"
        )
        data = transport(url)
        if len(data.splitlines()) < 2 or b"3D" not in data.splitlines()[1]:
            raise ValueError("PubChem response does not declare 3D coordinates")
        if data.count(b"$$$$") != 1:
            raise ValueError(
                "PubChem returned multiple/no conformers; specify one CID or download explicitly"
            )
        return sdf_to_qvf(data)
    raise ValueError("Select RCSB, AlphaFold, PubChem CID or PubChem name")


def fetch_structure(
    provider: str, identifier: str, *, transport: Callable[[str], bytes] = _download
) -> io.BytesIO:
    """Fetch on explicit invocation and retain source response identities."""
    receipts = []

    def recorded(url: str) -> bytes:
        data = transport(url)
        receipts.append(
            {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        )
        return data

    result = _fetch_structure(provider, identifier, transport=recorded)
    output = io.BytesIO()
    with (
        zipfile.ZipFile(result) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for name in source.namelist():
            content = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(content)
                manifest.setdefault("provenance", {})["database_fetch"] = {
                    "provider": provider,
                    "identifier": identifier.strip(),
                    "responses": receipts,
                }
                content = json.dumps(manifest, allow_nan=False).encode()
            target.writestr(name, content)
    output.seek(0)
    return output
