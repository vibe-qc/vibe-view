"""Consume QC spectral fingerprints without recomputing their graph physics."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

CHANNELS = ("electrostatic", "bonding", "steric", "dispersion")


@dataclass
class ChemicalSpace:
    records: list[dict[str, Any]]
    channels: np.ndarray
    vectors: np.ndarray
    points: np.ndarray
    variant: str
    parameters: dict[str, Any]

    @classmethod
    def load(cls, data: dict[str, Any], variant: str = "quantile-scale") -> ChemicalSpace:
        if data.get("schema") != "vibeview.chemical-space/1":
            raise ValueError("Expected vibeview.chemical-space/1 collection")
        records = data.get("records")
        if not isinstance(records, list) or not 2 <= len(records) <= 100000:
            raise ValueError("Supply 2 to 100000 structure records")
        channels, vectors, parameters, ids = [], [], None, set()
        for record in records:
            ident = record["id"]
            if not isinstance(ident, str) or ident in ids or any(ord(c) < 32 for c in ident):
                raise ValueError("Structure IDs must be unique strings")
            ids.add(ident)
            payload = record["fingerprint"]
            if payload.get("schema") != "vibeqc.spectral-fingerprint/1":
                raise ValueError("Expected versioned QC producer fingerprint")
            if payload.get("variant") != variant or variant not in ("toney2026", "quantile-scale"):
                raise ValueError("Select the supplied variant; mixed variants are not comparable")
            param = payload["parameters"]
            if parameters is None:
                parameters = param
            if param != parameters or payload["channel_order"] != list(CHANNELS):
                raise ValueError("Mixed fingerprint parameters or channel order")
            if (
                param.get("similarity") != "1/(1+euclidean)"
                or param.get("graph") != "complete"
                or param.get("scale_weight") != (1.0 if variant == "quantile-scale" else 0.0)
            ):
                raise ValueError("Unsupported producer parameter contract")
            table_hash = param.get("table_sha256", "")
            if (
                not isinstance(table_hash, str)
                or len(table_hash) != 64
                or any(c not in "0123456789abcdef" for c in table_hash)
                or param.get("singleton") != "property-first-zero-padded"
            ):
                raise ValueError("Invalid producer property-table identity")
            n = param["n_eigs"]
            if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 128:
                raise ValueError("Invalid spectrum width")
            values = np.asarray(payload["channels"], dtype=float)
            if (
                values.shape != (4, n + int(variant == "quantile-scale"))
                or not np.isfinite(values).all()
            ):
                raise ValueError("Invalid channel shape or nonfinite value")
            symbols = record["symbols"]
            xyz = np.asarray(record["coordinates_angstrom"], dtype=float)
            if (
                not isinstance(symbols, list)
                or not 1 <= len(symbols) <= 1000
                or any(not isinstance(s, str) or not s.isalpha() for s in symbols)
                or xyz.shape != (len(symbols), 3)
                or not np.isfinite(xyz).all()
                or payload["atom_count"] != len(symbols)
            ):
                raise ValueError("Invalid structure geometry")
            digest = hashlib.sha256(
                json.dumps(
                    {"symbols": symbols, "coordinates_angstrom": xyz.tolist()},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if payload["geometry_sha256"] != digest:
                raise ValueError("Fingerprint geometry does not match displayed structure")
            weight, size = float(param["size_weight"]), float(payload["size_component"])
            if (
                not np.isfinite([weight, size]).all()
                or weight < 0
                or not np.isclose(size, weight * np.log(len(symbols)), rtol=0, atol=1e-12)
                or (variant == "toney2026" and weight != 0)
            ):
                raise ValueError("Invalid size component")
            channels.append(values)
            vectors.append(np.append(values.ravel(), size) if weight else values.ravel())
        matrix = np.asarray(vectors)
        try:
            with np.errstate(over="raise", invalid="raise"):
                centered = matrix - matrix.mean(axis=0)
                covariance = centered.T @ centered
        except FloatingPointError as exc:
            raise ValueError("Fingerprint magnitudes exceed projection range") from exc
        # Small feature covariance, not an N by N distance matrix.
        _, axes = np.linalg.eigh(covariance)
        points = centered @ axes[:, -2:][:, ::-1]
        return cls(records, np.asarray(channels), matrix, points, variant, parameters or {})

    def pair(self, first: int, second: int) -> dict[str, float]:
        self._index(first)
        self._index(second)
        result = {
            name: float(1 / (1 + np.linalg.norm(a - b)))
            for name, a, b in zip(
                CHANNELS, self.channels[first], self.channels[second], strict=True
            )
        }
        result["overall"] = float(
            1 / (1 + np.linalg.norm(self.vectors[first] - self.vectors[second]))
        )
        return result

    def _index(self, index: int) -> None:
        if (
            isinstance(index, bool)
            or not isinstance(index, (int, np.integer))
            or not 0 <= index < len(self.records)
        ):
            raise ValueError("Structure index out of range")

    def neighbours(self, index: int, k: int = 10) -> list[int]:
        self._index(index)
        if not 1 <= k <= 100:
            raise ValueError("Choose 1 to 100 neighbours")
        distances = np.linalg.norm(self.vectors - self.vectors[index], axis=1)
        distances[index] = np.inf
        return [int(i) for i in np.argsort(distances, kind="stable")[: min(k, len(distances) - 1)]]

    def pixels(self) -> np.ndarray:
        span = np.ptp(self.points, axis=0)
        scaled = (self.points - self.points.min(axis=0)) / np.where(span > 0, span, 1)
        return np.asarray(16 + scaled * np.array([608, 368]))

    def nearest_point(self, x: float, y: float) -> int:
        if not np.isfinite([x, y]).all():
            raise ValueError("Invalid point coordinates")
        return int(np.argmin(np.linalg.norm(self.pixels() - [x, y], axis=1)))

    def image(self, colour: str = "group", selected: int = -1) -> str:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (640, 400), "white")
        draw = ImageDraw.Draw(image)
        palette = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]
        groups = sorted({str(r.get("group", "")) for r in self.records})
        group_colours = {name: palette[i % len(palette)] for i, name in enumerate(groups)}
        numeric = None
        if colour != "group":
            numeric = np.asarray(
                [r.get("properties", {}).get(colour, np.nan) for r in self.records], dtype=float
            )
            finite = numeric[np.isfinite(numeric)]
            if not len(finite):
                raise ValueError("Selected property has no finite numeric values")
            lo, hi = finite.min(), finite.max()
        fill: str | tuple[int, int, int]
        for i, ((x, y), record) in enumerate(zip(self.pixels(), self.records, strict=True)):
            if numeric is None:
                fill = group_colours[str(record.get("group", ""))]
            elif np.isfinite(numeric[i]):
                t = float((numeric[i] - lo) / (hi - lo)) if hi > lo else 0.5
                fill = (int(220 * t), 80, int(220 * (1 - t)))
            else:
                fill = "#999999"
            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=fill)
        if 0 <= selected < len(self.records):
            x, y = self.pixels()[selected]
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline="black", width=2)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
