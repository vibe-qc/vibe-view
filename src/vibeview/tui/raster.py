"""Pure-numpy z-buffered software rasterizer.

No OpenGL, no VTK render window, no display server — the whole pipeline is
numpy array arithmetic into an RGB pixel buffer plus a depth buffer. That is
the point: the terminal viewer has to run on a headless compute node where
``pyvista.Plotter(off_screen=True)`` would need a GL/OSMesa context that
isn't there.

Coordinate conventions
----------------------
* **World** — angstroms, right-handed, y up. Same frame the structure
  renderer and the QVF ``structure`` section use.
* **Camera** — the world rotated by :attr:`Camera.rotation` about the scene
  centre, then translated so the camera sits at the origin looking down
  **+z**. Visible geometry therefore has ``z > 0``, and larger ``z`` is
  further away.
* **Screen** — pixels, origin top-left, ``y`` growing downward (so the
  screen-space vertical is the negated world ``y``).

A surface normal points *outward*, toward the camera, so a front-facing
normal has ``nz < 0``. Every primitive normalizes to that convention before
shading, and :func:`shade` assumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Geometry closer than this to the camera plane is dropped rather than
# projected: 1/z explodes and a single atom behind the eye would otherwise
# smear across the whole canvas.
_NEAR = 0.05

# Samples processed per vectorized chunk. Bounds peak memory at a few tens
# of MB regardless of how large a mesh or how zoomed-in a sphere gets.
_CHUNK = 1 << 21

# Blinn-Phong parameters. Direction *toward* the light, in camera space:
# up, slightly to the left, and in front of the scene (negative z is
# toward the viewer), which puts the highlight where a reader expects it.
_LIGHT = np.array([-0.35, 0.55, -0.76])
_LIGHT /= np.linalg.norm(_LIGHT)
_VIEW = np.array([0.0, 0.0, -1.0])  # toward the viewer
_HALF = _LIGHT + _VIEW
_HALF /= np.linalg.norm(_HALF)

_AMBIENT = 0.34
_DIFFUSE = 0.62
_SPECULAR = 0.38
_SHININESS = 24.0


def rotation_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Extrinsic X-then-Y-then-Z rotation matrix."""
    cx, sx = np.cos(rx), np.sin(rx)
    cy, sy = np.cos(ry), np.sin(ry)
    cz, sz = np.cos(rz), np.sin(rz)
    mx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    my = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    mz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return mz @ my @ mx


def shade(normals: np.ndarray, colors: np.ndarray) -> np.ndarray:
    """Blinn-Phong shade unit ``normals`` (N,3) against ``colors`` (N,3).

    Normals must already face the camera (``nz <= 0``). Returns uint8 RGB.
    """
    n_dot_l = np.maximum(0.0, normals @ _LIGHT)
    n_dot_h = np.maximum(0.0, normals @ _HALF)
    intensity = np.minimum(1.0, _AMBIENT + _DIFFUSE * n_dot_l)
    specular = _SPECULAR * np.power(n_dot_h, _SHININESS)
    lit = colors * intensity[:, None] + 255.0 * specular[:, None]
    return np.clip(lit, 0.0, 255.0).astype(np.uint8)


@dataclass
class Camera:
    """Orbit camera: a rotation about the scene centre plus a pull-back.

    ``fov`` is the projection scale factor, not an angle — a screen-space
    half-extent of ``fov`` world units at unit depth.
    """

    rotation: np.ndarray = field(default_factory=lambda: rotation_matrix(-0.25, -0.55, 0.0))
    distance: float = 12.0
    pan: tuple[float, float] = (0.0, 0.0)
    fov: float = 1.5
    center: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def to_camera(self, points: np.ndarray) -> np.ndarray:
        """World (N,3) angstroms to camera-frame (N,3)."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        out = (self.rotation @ (pts - self.center).T).T
        out[:, 0] += self.pan[0]
        out[:, 1] += self.pan[1]
        out[:, 2] += self.distance
        return out

    def rotate_by(self, dx: float, dy: float) -> None:
        """Compose an incremental screen-space drag onto the rotation."""
        self.rotation = rotation_matrix(dy, dx, 0.0) @ self.rotation

    def directions(self, vectors: np.ndarray) -> np.ndarray:
        """Rotate direction vectors (no translation) into camera frame."""
        vecs = np.atleast_2d(np.asarray(vectors, dtype=np.float64))
        return (self.rotation @ vecs.T).T


class Canvas:
    """An RGB pixel buffer with a depth buffer and drawing primitives.

    All ``draw_*`` methods take **camera-frame** coordinates (see the module
    docstring) so a caller can transform a whole scene once.
    """

    def __init__(
        self,
        width: int,
        height: int,
        background: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.background = np.array(background, dtype=np.uint8)
        # A square molecule should look square: the projection scale is tied
        # to the smaller axis so aspect is preserved on any terminal shape.
        self.scale = min(self.width, self.height) / 2.0
        self.pixels = np.empty((self.height, self.width, 3), dtype=np.uint8)
        self.zbuf = np.empty((self.height, self.width), dtype=np.float64)
        self.covered = np.empty((self.height, self.width), dtype=bool)
        self.clear()

    def clear(self) -> None:
        self.pixels[:] = self.background
        self.zbuf[:] = np.inf
        self.covered[:] = False

    # ── projection ────────────────────────────────────────────────────────

    def project(self, cam_points: np.ndarray, fov: float) -> tuple[np.ndarray, np.ndarray]:
        """Camera-frame (N,3) to screen ``(xy (N,2), z (N,))``.

        Points at or behind the near plane get ``nan`` screen coordinates;
        callers filter on the returned ``z``.
        """
        pts = np.atleast_2d(np.asarray(cam_points, dtype=np.float64))
        z = pts[:, 2]
        safe = np.where(z > _NEAR, z, np.nan)
        xy = np.empty((len(pts), 2), dtype=np.float64)
        xy[:, 0] = self.width / 2.0 + pts[:, 0] * fov / safe * self.scale
        xy[:, 1] = self.height / 2.0 - pts[:, 1] * fov / safe * self.scale
        return xy, z

    # ── depth-tested composite ────────────────────────────────────────────

    def composite(
        self,
        px: np.ndarray,
        py: np.ndarray,
        pz: np.ndarray,
        rgb: np.ndarray,
    ) -> None:
        """Depth-test and write a batch of shaded samples.

        Samples may collide — several within this batch landing on one pixel,
        or a batch pixel landing on something already drawn. Both are resolved
        the same way: nearest ``pz`` wins. Within the batch that is a lexsort
        on ``(z, pixel_id)`` keeping the first row of each pixel group, which
        is O(n log n) and, unlike a scatter assignment, order-independent.
        """
        px = np.asarray(px, dtype=np.int64).ravel()
        py = np.asarray(py, dtype=np.int64).ravel()
        pz = np.asarray(pz, dtype=np.float64).ravel()
        rgb = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)

        keep = (
            (px >= 0)
            & (px < self.width)
            & (py >= 0)
            & (py < self.height)
            & np.isfinite(pz)
        )
        if not keep.any():
            return
        px, py, pz, rgb = px[keep], py[keep], pz[keep], rgb[keep]

        pid = py * self.width + px
        order = np.lexsort((pz, pid))
        pid_sorted = pid[order]
        first = np.empty(len(pid_sorted), dtype=bool)
        first[0] = True
        np.not_equal(pid_sorted[1:], pid_sorted[:-1], out=first[1:])
        idx = order[first]

        px, py, pz, rgb = px[idx], py[idx], pz[idx], rgb[idx]
        nearer = pz < self.zbuf[py, px]
        if not nearer.any():
            return
        py, px, pz, rgb = py[nearer], px[nearer], pz[nearer], rgb[nearer]
        self.zbuf[py, px] = pz
        self.pixels[py, px] = rgb
        self.covered[py, px] = True

    # ── primitives ────────────────────────────────────────────────────────

    def draw_spheres(
        self,
        centers: np.ndarray,
        radii: np.ndarray,
        colors: np.ndarray,
        fov: float = 1.5,
    ) -> None:
        """Shaded spheres from camera-frame centres and world-space radii.

        Rasterized analytically rather than as a triangulated ball: at
        terminal resolution a sphere is a disc of a few pixels, and solving
        ``nz = sqrt(1 - dx^2 - dy^2)`` per pixel is both cheaper and smoother
        than any mesh that would survive the downsample to braille dots.

        Spheres are bucketed by integer projected radius so each bucket
        rasterizes as one vectorized (n_spheres, n_samples) batch.
        """
        centers = np.atleast_2d(np.asarray(centers, dtype=np.float64))
        if len(centers) == 0:
            return
        radii = np.asarray(radii, dtype=np.float64).ravel()
        colors = np.asarray(colors, dtype=np.float64).reshape(-1, 3)

        xy, z = self.project(centers, fov)
        pr = radii * fov / np.where(z > _NEAR, z, np.nan) * self.scale
        # Sub-pixel spheres would rasterize to nothing; clamp so a distant
        # atom stays a visible dot instead of silently vanishing.
        visible = np.isfinite(pr) & (z > _NEAR)
        if not visible.any():
            return
        pr = np.clip(pr[visible], 0.6, max(self.width, self.height))
        sx, sy = xy[visible, 0], xy[visible, 1]
        sz = z[visible]
        rw = radii[visible]
        rgb = colors[visible]

        buckets = np.ceil(pr).astype(np.int64)
        for bucket in np.unique(buckets):
            sel = buckets == bucket
            self._sphere_bucket(
                int(bucket), sx[sel], sy[sel], sz[sel], pr[sel], rw[sel], rgb[sel]
            )

    def _sphere_bucket(self, radius_px, sx, sy, sz, pr, rw, rgb) -> None:
        span = np.arange(-radius_px - 1, radius_px + 2, dtype=np.float64)
        ox, oy = np.meshgrid(span, span, indexing="xy")
        ox, oy = ox.ravel(), oy.ravel()
        n_samples = len(ox)
        if n_samples == 0:
            return

        stride = max(1, _CHUNK // n_samples)
        for start in range(0, len(sx), stride):
            end = min(start + stride, len(sx))
            sl = slice(start, end)
            n_here = end - start

            gx = np.round(sx[sl])[:, None] + ox[None, :]
            gy = np.round(sy[sl])[:, None] + oy[None, :]
            dx = (gx - sx[sl][:, None]) / pr[sl][:, None]
            dy = (gy - sy[sl][:, None]) / pr[sl][:, None]
            d2 = dx * dx + dy * dy
            inside = d2 <= 1.0
            if not inside.any():
                continue

            nz_mag = np.sqrt(np.maximum(0.0, 1.0 - d2))
            # Screen y grows downward, world y upward: negate dy for the
            # shading normal. nz is negative — pointing back at the camera.
            normals = np.stack([dx[inside], -dy[inside], -nz_mag[inside]], axis=1)
            depth = (sz[sl][:, None] - rw[sl][:, None] * nz_mag)[inside]
            base = np.broadcast_to(rgb[sl][:, None, :], (n_here, n_samples, 3))[inside]
            self.composite(gx[inside], gy[inside], depth, shade(normals, base))

    def draw_cylinders(
        self,
        starts: np.ndarray,
        ends: np.ndarray,
        radius: float,
        colors_a: np.ndarray,
        colors_b: np.ndarray,
        fov: float = 1.5,
    ) -> None:
        """Shaded bond cylinders, split half-and-half between two colours.

        Approximated as a screen-space ribbon carrying a cylindrical normal:
        exact enough at braille resolution, and it keeps the whole bond list
        in one vectorized batch instead of meshing each bond.
        """
        starts = np.atleast_2d(np.asarray(starts, dtype=np.float64))
        ends = np.atleast_2d(np.asarray(ends, dtype=np.float64))
        if len(starts) == 0:
            return
        colors_a = np.asarray(colors_a, dtype=np.float64).reshape(-1, 3)
        colors_b = np.asarray(colors_b, dtype=np.float64).reshape(-1, 3)

        xy1, z1 = self.project(starts, fov)
        xy2, z2 = self.project(ends, fov)
        ok = (
            (z1 > _NEAR)
            & (z2 > _NEAR)
            & np.isfinite(xy1).all(axis=1)
            & np.isfinite(xy2).all(axis=1)
        )
        if not ok.any():
            return
        xy1, xy2, z1, z2 = xy1[ok], xy2[ok], z1[ok], z2[ok]
        colors_a, colors_b = colors_a[ok], colors_b[ok]

        delta = xy2 - xy1
        length = np.hypot(delta[:, 0], delta[:, 1])
        alive = length > 0.5
        if not alive.any():
            return
        xy1, xy2, z1, z2 = xy1[alive], xy2[alive], z1[alive], z2[alive]
        colors_a, colors_b = colors_a[alive], colors_b[alive]
        delta, length = delta[alive], length[alive]

        mid_z = 0.5 * (z1 + z2)
        pr = np.clip(radius * fov / mid_z * self.scale, 0.5, None)
        perp = np.stack([-delta[:, 1] / length, delta[:, 0] / length], axis=1)

        # One global sample grid keeps this a single batch; oversampling a
        # short bond only produces duplicate pixels, which the depth
        # composite collapses.
        n_axis = int(min(512, max(2, np.ceil(length.max()) * 2 + 2)))
        n_cross = int(min(128, max(3, np.ceil(pr.max() * 2) + 3)))
        t = np.linspace(0.0, 1.0, n_axis)[None, :, None]
        u = np.linspace(-1.0, 1.0, n_cross)[None, None, :]

        stride = max(1, _CHUNK // (n_axis * n_cross))
        for start in range(0, len(xy1), stride):
            end = min(start + stride, len(xy1))
            sl = slice(start, end)
            n_here = end - start

            cx = xy1[sl, 0][:, None, None] + delta[sl, 0][:, None, None] * t
            cy = xy1[sl, 1][:, None, None] + delta[sl, 1][:, None, None] * t
            cz = z1[sl][:, None, None] + (z2[sl] - z1[sl])[:, None, None] * t

            gx = cx + perp[sl, 0][:, None, None] * u * pr[sl][:, None, None]
            gy = cy + perp[sl, 1][:, None, None] * u * pr[sl][:, None, None]

            nz_mag = np.sqrt(np.maximum(0.0, 1.0 - u * u))
            nx = np.broadcast_to(perp[sl, 0][:, None, None] * u, (n_here, n_axis, n_cross))
            ny = np.broadcast_to(-perp[sl, 1][:, None, None] * u, (n_here, n_axis, n_cross))
            nzb = np.broadcast_to(-nz_mag, (n_here, n_axis, n_cross))
            depth = cz - radius * nz_mag

            normals = np.stack([nx.ravel(), ny.ravel(), nzb.ravel()], axis=1)
            norm = np.linalg.norm(normals, axis=1, keepdims=True)
            normals /= np.where(norm > 1e-12, norm, 1.0)

            first_half = np.broadcast_to(t < 0.5, (n_here, n_axis, n_cross)).ravel()
            ca = np.broadcast_to(colors_a[sl][:, None, None, :], (n_here, n_axis, n_cross, 3))
            cb = np.broadcast_to(colors_b[sl][:, None, None, :], (n_here, n_axis, n_cross, 3))
            base = np.where(first_half[:, None], ca.reshape(-1, 3), cb.reshape(-1, 3))

            self.composite(
                np.round(gx).ravel(), np.round(gy).ravel(), depth.ravel(), shade(normals, base)
            )

    def draw_mesh(
        self,
        vertices: np.ndarray,
        faces: np.ndarray,
        normals: np.ndarray,
        color: tuple[int, int, int],
        fov: float = 1.5,
        opacity: float = 1.0,
    ) -> None:
        """Rasterize a camera-frame triangle mesh (isosurfaces, ribbons).

        Rendered **two-sided**: an isosurface need not be closed — a Fermi
        surface is an open sheet clipped by the Brillouin-zone boundary, and
        back-face culling would erase it from one side of every viewing axis.
        Per-sample normals are flipped toward the camera instead, which keeps
        shading correct on both faces at the cost of rasterizing the hidden
        one (the depth test discards it on closed lobes).

        Triangles are sampled on a barycentric grid whose density is chosen
        per screen-space bounding box, so a lobe spanning the viewport and a
        triangle covering two pixels each cost about what they should.
        """
        vertices = np.atleast_2d(np.asarray(vertices, dtype=np.float64))
        faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
        if len(faces) == 0 or len(vertices) == 0:
            return
        normals = np.asarray(normals, dtype=np.float64).reshape(-1, 3)

        xy, z = self.project(vertices, fov)
        good = (z > _NEAR) & np.isfinite(xy).all(axis=1)
        face_ok = good[faces].all(axis=1)
        faces = faces[face_ok]
        if len(faces) == 0:
            return

        s = np.stack([xy[faces[:, 0]], xy[faces[:, 1]], xy[faces[:, 2]]], axis=1)  # (F,3,2)
        fz = np.stack([z[faces[:, 0]], z[faces[:, 1]], z[faces[:, 2]]], axis=1)  # (F,3)
        fn = np.stack(
            [normals[faces[:, 0]], normals[faces[:, 1]], normals[faces[:, 2]]], axis=1
        )  # (F,3,3)

        extent = s.max(axis=1) - s.min(axis=1)
        span = np.maximum(extent[:, 0], extent[:, 1])
        # Sample count per triangle edge: enough to leave no pinholes at one
        # sample per pixel, capped so a single huge triangle can't blow up.
        density = np.clip(np.ceil(span).astype(np.int64) + 2, 2, 48)
        # Bucket to powers of two: a handful of vectorized batches instead of
        # one per distinct triangle size.
        buckets = 1 << np.ceil(np.log2(density)).astype(np.int64)

        base_rgb = np.array(color, dtype=np.float64)
        for bucket in np.unique(buckets):
            sel = buckets == bucket
            self._mesh_bucket(int(bucket), s[sel], fz[sel], fn[sel], base_rgb, opacity)

    def _mesh_bucket(self, n_edge, s, fz, fn, base_rgb, opacity) -> None:
        steps = np.linspace(0.0, 1.0, max(2, n_edge))
        uu, vv = np.meshgrid(steps, steps, indexing="ij")
        keep = (uu + vv) <= 1.0 + 1e-9
        u = uu[keep][None, :]
        v = vv[keep][None, :]
        n_samples = u.shape[1]
        if n_samples == 0:
            return

        stride = max(1, _CHUNK // n_samples)
        for start in range(0, len(s), stride):
            end = min(start + stride, len(s))
            sl = slice(start, end)
            p0, p1, p2 = s[sl, 0], s[sl, 1], s[sl, 2]
            z0, z1, z2 = fz[sl, 0], fz[sl, 1], fz[sl, 2]
            n0, n1, n2 = fn[sl, 0], fn[sl, 1], fn[sl, 2]

            gx = p0[:, 0:1] + u * (p1[:, 0:1] - p0[:, 0:1]) + v * (p2[:, 0:1] - p0[:, 0:1])
            gy = p0[:, 1:2] + u * (p1[:, 1:2] - p0[:, 1:2]) + v * (p2[:, 1:2] - p0[:, 1:2])
            gz = z0[:, None] + u * (z1 - z0)[:, None] + v * (z2 - z0)[:, None]

            nx = n0[:, 0:1] + u * (n1[:, 0:1] - n0[:, 0:1]) + v * (n2[:, 0:1] - n0[:, 0:1])
            ny = n0[:, 1:2] + u * (n1[:, 1:2] - n0[:, 1:2]) + v * (n2[:, 1:2] - n0[:, 1:2])
            nz = n0[:, 2:3] + u * (n1[:, 2:3] - n0[:, 2:3]) + v * (n2[:, 2:3] - n0[:, 2:3])

            normals = np.stack([nx.ravel(), ny.ravel(), nz.ravel()], axis=1)
            norm = np.linalg.norm(normals, axis=1, keepdims=True)
            normals /= np.where(norm > 1e-12, norm, 1.0)
            # Two-sided: flip anything pointing away from the camera.
            normals[normals[:, 2] > 0.0] *= -1.0

            rgb = np.broadcast_to(base_rgb, (normals.shape[0], 3))
            shaded = shade(normals, rgb)
            if opacity < 1.0:
                shaded = (
                    shaded.astype(np.float64) * opacity
                    + self.background.astype(np.float64) * (1.0 - opacity)
                ).astype(np.uint8)
            self.composite(np.round(gx).ravel(), np.round(gy).ravel(), gz.ravel(), shaded)

    def draw_lines(
        self,
        starts: np.ndarray,
        ends: np.ndarray,
        color: tuple[int, int, int],
        fov: float = 1.5,
        width: float = 1.0,
    ) -> None:
        """Flat-shaded 3D line segments (cell edges, axis indicators)."""
        starts = np.atleast_2d(np.asarray(starts, dtype=np.float64))
        ends = np.atleast_2d(np.asarray(ends, dtype=np.float64))
        if len(starts) == 0:
            return

        xy1, z1 = self.project(starts, fov)
        xy2, z2 = self.project(ends, fov)
        ok = (
            (z1 > _NEAR)
            & (z2 > _NEAR)
            & np.isfinite(xy1).all(axis=1)
            & np.isfinite(xy2).all(axis=1)
        )
        if not ok.any():
            return
        xy1, xy2, z1, z2 = xy1[ok], xy2[ok], z1[ok], z2[ok]

        delta = xy2 - xy1
        length = np.hypot(delta[:, 0], delta[:, 1])
        n_axis = int(min(2048, max(2, np.ceil(length.max()) * 2 + 2)))
        t = np.linspace(0.0, 1.0, n_axis)[None, :]

        gx = xy1[:, 0][:, None] + delta[:, 0][:, None] * t
        gy = xy1[:, 1][:, None] + delta[:, 1][:, None] * t
        gz = z1[:, None] + (z2 - z1)[:, None] * t

        half = max(0, int(round(width)) // 2)
        offsets = range(-half, half + 1)
        rgb_flat = np.broadcast_to(np.array(color, dtype=np.uint8), (gx.size, 3))
        for ox in offsets:
            for oy in offsets:
                self.composite(
                    np.round(gx + ox).ravel(), np.round(gy + oy).ravel(), gz.ravel(), rgb_flat
                )

    def draw_pixels(
        self,
        xs: np.ndarray,
        ys: np.ndarray,
        color: tuple[int, int, int],
    ) -> None:
        """Paint flat 2D screen pixels at the near plane (plot overlays)."""
        xs = np.asarray(xs).ravel()
        ys = np.asarray(ys).ravel()
        if xs.size == 0:
            return
        depth = np.full(xs.size, -1.0)
        rgb = np.broadcast_to(np.array(color, dtype=np.uint8), (xs.size, 3))
        self.composite(xs, ys, depth, rgb)
