"""
build_encore.py -- Encore Beach Club (Wynn Las Vegas), stylized low-poly 3D venue map.

Builds the entire scene from scratch with bpy, saves a .blend, exports a Draco-compressed
GLB with WebP textures and renders two previews (top-down and perspective).

Usage (Blender binary):
    blender -b --python build_encore.py -- [--stage blockout|full] [--no-render] [--out DIR] [--samples N]
Usage (bpy wheel, `pip install bpy`):
    python build_encore.py -- [same options]

Conventions
    * Units: metres.  +Y is "forward" (from the DJ booth toward the pools), +Z is up.
    * Origin (0,0,0) is the centre of the DJ booth.  South (left on the plan) is -X, North is +X.
    * Every bookable table is one object named  T_<number>  with custom properties
      `type`, `number`, `zone`.  Zones are Blender collections.
    * Furniture is built once per type and reused as linked duplicates (shared mesh data),
      so the GLB contains one mesh per furniture type.
    * Flat colours only: one 64x64 palette atlas texture (`Atlas` material) + one `Water`
      material.  Each face's UVs point at a palette cell.

Layout source: the 2026 Encore Beach Club table map (refs/floorplan-map-2026.png).
The map has no scale; SCALE below is an estimate chosen so the outdoor footprint is
roughly 5,500 m2.  See README.md for known inaccuracies.
"""
import argparse
import math
import os
import sys
import time

import bpy
import bmesh
from mathutils import Vector

# --------------------------------------------------------------------------------------
# Floor-plan calibration (pixel space of refs/floorplan-map-2026.png, 785 x 1024 px)
# --------------------------------------------------------------------------------------
SCALE = 0.13                 # metres per pixel  (ESTIMATE)
ORIGIN_PX = (400.0, 548.0)   # DJ booth centre on the plan
ARC_C_PX = (400.0, 480.0)    # centre of the crescent of cabanas
R_LOWER_PX = 342.0           # radius of the lower cabana row
R_LOWER_BUNG_PX = 330.0      # radius of the lower bungalows
R_UPPER_PX = 410.0           # radius of the upper cabana row
R_UPPER_BUNG_PX = 395.0      # radius of the upper bungalows
R_FACADE_PX = 440.0          # back facade wall


def P(px, py):
    """Plan pixel -> world XY (metres)."""
    return ((px - ORIGIN_PX[0]) * SCALE, (ORIGIN_PX[1] - py) * SCALE)


def L(px):
    """Plan pixel length -> metres."""
    return px * SCALE


def mirror(px):
    """Mirror a plan x pixel across the central axis (south <-> north)."""
    return 2 * ORIGIN_PX[0] - px


def arc_xy(r_px, deg):
    """Point on the crescent arc (angle measured counter-clockwise from +X, 90 = straight up)."""
    px = ARC_C_PX[0] + r_px * math.cos(math.radians(deg))
    py = ARC_C_PX[1] - r_px * math.sin(math.radians(deg))
    return P(px, py)


def arc_rot(deg):
    """Z rotation so that an object's local -Y faces the arc centre (front toward the pool)."""
    return math.radians(deg - 90.0)


# --------------------------------------------------------------------------------------
# Palette atlas (4 x 4 cells).  Index -> colour.  UVs of every face point at a cell centre.
# --------------------------------------------------------------------------------------
PALETTE = [
    ("ivory",  (0.93, 0.89, 0.80)),   # 0 classical buildings
    ("deck",   (0.86, 0.80, 0.69)),   # 1 sand / travertine deck
    ("walk",   (0.72, 0.69, 0.64)),   # 2 walkways
    ("orange", (0.96, 0.46, 0.10)),   # 3 cushions
    ("yellow", (0.95, 0.80, 0.25)),   # 4 beach couch legend colour
    ("red",    (0.80, 0.10, 0.10)),   # 5 umbrellas / awnings / daybeds legend
    ("white",  (0.97, 0.97, 0.95)),   # 6 wicker / marble / umbrella stripes
    ("water",  (0.25, 0.75, 0.80)),   # 7 pool coping tint (water itself uses Water material)
    ("green",  (0.18, 0.47, 0.22)),   # 8 palm fronds / large dance floor
    ("trunk",  (0.45, 0.33, 0.20)),   # 9 palm trunks
    ("purple", (0.55, 0.30, 0.70)),   # 10 dance floor couches
    ("pink",   (0.90, 0.45, 0.60)),   # 11 medium dance floor / casa playa
    ("blue",   (0.20, 0.45, 0.85)),   # 12 lily pads legend / small dance floor
    ("dark",   (0.20, 0.20, 0.22)),   # 13 stage, DJ booth, bars top
    ("teal",   (0.45, 0.75, 0.72)),   # 14 bungalow legend colour
    ("marble", (0.82, 0.82, 0.80)),   # 15 bar counters / pool basin
]
CI = {name: i for i, (name, _) in enumerate(PALETTE)}


def palette_uv(idx):
    col, row = idx % 4, idx // 4
    return ((col + 0.5) / 4.0, (row + 0.5) / 4.0)


def make_palette_image(out_dir):
    size = 64
    img = bpy.data.images.new("encore_palette", size, size, alpha=False)
    px = [0.0] * (size * size * 4)
    for y in range(size):
        for x in range(size):
            idx = (y // 16) * 4 + (x // 16)
            r, g, b = PALETTE[idx][1]
            o = (y * size + x) * 4
            px[o:o + 4] = [r, g, b, 1.0]
    img.pixels = px
    path = os.path.join(out_dir, "palette.png")
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    img.pack()
    return img


def make_materials(out_dir):
    img = make_palette_image(out_dir)

    atlas = bpy.data.materials.new("Atlas")
    atlas.use_nodes = True
    nt = atlas.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.8
    bsdf.inputs["Specular IOR Level"].default_value = 0.2
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    water = bpy.data.materials.new("Water")
    water.use_nodes = True
    wb = water.node_tree.nodes["Principled BSDF"]
    wb.inputs["Base Color"].default_value = (0.15, 0.70, 0.78, 1.0)
    wb.inputs["Roughness"].default_value = 0.05
    wb.inputs["Alpha"].default_value = 0.72
    water.blend_method = "BLEND"
    if hasattr(water, "surface_render_method"):
        water.surface_render_method = "BLENDED"
    return atlas, water


# --------------------------------------------------------------------------------------
# bmesh helpers.  Every primitive stamps the palette UV on the faces it creates.
# --------------------------------------------------------------------------------------
def _stamp(bm, faces, color):
    uv_layer = bm.loops.layers.uv.verify()
    u, v = palette_uv(CI[color])
    for f in faces:
        f.material_index = CI[color]
        for loop in f.loops:
            loop[uv_layer].uv = (u, v)


def _faces_of(verts):
    return {f for vert in verts for f in vert.link_faces}


def bm_box(bm, center, size, color, rot_z=0.0):
    r = bmesh.ops.create_cube(bm, size=1.0)
    verts = r["verts"]
    bmesh.ops.scale(bm, vec=Vector(size), verts=verts)
    if rot_z:
        from mathutils import Matrix
        bmesh.ops.rotate(bm, cent=(0, 0, 0), matrix=Matrix.Rotation(rot_z, 3, "Z"), verts=verts)
    bmesh.ops.translate(bm, vec=Vector(center), verts=verts)
    faces = _faces_of(verts)
    _stamp(bm, faces, color)
    return faces


def bm_cyl(bm, center, radius, height, color, segments=12, radius2=None, color_alt=None):
    r2 = radius if radius2 is None else radius2
    r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments,
                              radius1=radius, radius2=r2, depth=height)
    verts = r["verts"]
    bmesh.ops.translate(bm, vec=Vector(center), verts=verts)
    faces = _faces_of(verts)
    _stamp(bm, faces, color)
    if color_alt:  # alternate side faces (umbrella stripes)
        bm.normal_update()
        side = sorted((f for f in faces if len(f.verts) <= 4 and abs(f.normal.z) < 0.7),
                      key=lambda f: math.atan2(f.calc_center_median().y - center[1],
                                               f.calc_center_median().x - center[0]))
        _stamp(bm, side[1::2], color_alt)
    return faces


def bm_prism(bm, poly_xy, z0, z1, color, top=True, bottom=True, sides=True):
    """Extrude a 2D polygon (list of (x, y)) from z0 to z1."""
    verts = [bm.verts.new((x, y, z0)) for x, y in poly_xy]
    base = bm.faces.new(verts)
    bm.faces.ensure_lookup_table()
    res = bmesh.ops.extrude_face_region(bm, geom=[base])
    new_verts = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=(0, 0, z1 - z0), verts=new_verts)
    faces = _faces_of(verts) | _faces_of(new_verts)
    bmesh.ops.recalc_face_normals(bm, faces=list(faces))
    kill = []
    for f in faces:
        nz = f.normal.z
        if nz > 0.9 and not top:
            kill.append(f)
        elif nz < -0.9 and not bottom:
            kill.append(f)
        elif abs(nz) <= 0.9 and not sides:
            kill.append(f)
    if kill:
        bmesh.ops.delete(bm, geom=kill, context="FACES")
        faces = [f for f in faces if f.is_valid]
    _stamp(bm, faces, color)
    return faces


def bm_face(bm, poly_xy, z, color):
    verts = [bm.verts.new((x, y, z)) for x, y in poly_xy]
    f = bm.faces.new(verts)
    bmesh.ops.recalc_face_normals(bm, faces=[f])
    if f.normal.z < 0:
        bmesh.ops.reverse_faces(bm, faces=[f])
    _stamp(bm, [f], color)
    return f


def ring_poly(r1_px, r2_px, a0, a1, steps=8):
    """Ring segment polygon in world XY between two radii and two angles (degrees)."""
    pts = []
    for i in range(steps + 1):
        pts.append(arc_xy(r2_px, a0 + (a1 - a0) * i / steps))
    for i in range(steps, -1, -1):
        pts.append(arc_xy(r1_px, a0 + (a1 - a0) * i / steps))
    return pts


def rect_px(cx, cy, w, h):
    """Axis-aligned rectangle given in plan pixels (centre, width, height) -> world polygon."""
    x0, y0 = P(cx - w / 2, cy + h / 2)
    x1, y1 = P(cx + w / 2, cy - h / 2)
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# --------------------------------------------------------------------------------------
# Scene bookkeeping
# --------------------------------------------------------------------------------------
COLLS = {}
MATS = {}
LOOK = "stylized"          # "stylized" (atlas, web GLB) or "photo" (procedural materials, Cycles only)
PHOTO_MATS = []            # palette-ordered materials used in photo mode
PROTOS = {}
STATS = {"objects": 0}


def coll(name):
    if name not in COLLS:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
        COLLS[name] = c
    return COLLS[name]


def finish_mesh(name, bm, material="Atlas"):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    if LOOK == "photo" and material == "Atlas":
        for m in PHOTO_MATS:
            mesh.materials.append(m)
    else:
        mesh.materials.append(MATS[material])
        if LOOK != "photo":
            for poly in mesh.polygons:
                poly.material_index = 0
    return mesh


def add_obj(name, mesh, collection, loc=(0, 0, 0), rot_z=0.0, props=None):
    ob = bpy.data.objects.new(name, mesh)
    ob.location = loc
    ob.rotation_euler = (0, 0, rot_z)
    coll(collection).objects.link(ob)
    if props:
        for k, v in props.items():
            ob[k] = v
    STATS["objects"] += 1
    return ob


def proto(name, builder, material="Atlas"):
    """Build a furniture mesh once; later instances share it (linked duplicates)."""
    if name not in PROTOS:
        bm = bmesh.new()
        builder(bm)
        if LOOK == "photo" and name != "palm":
            bm.normal_update()
            edges = [e for e in bm.edges if e.is_boundary or
                     (len(e.link_faces) == 2 and e.calc_face_angle(0.0) > math.radians(30))]
            bmesh.ops.bevel(bm, geom=edges, offset=0.025, segments=2, affect="EDGES",
                            profile=0.6, material=-1)
        PROTOS[name] = finish_mesh("M_" + name, bm, material)
    return PROTOS[name]


def table(number, kind, zone, loc_xy, z=0.0, rot_z=0.0, proto_name=None):
    mesh = proto(proto_name or kind, FURNITURE[proto_name or kind])
    return add_obj("T_%s" % number, mesh, zone, (loc_xy[0], loc_xy[1], z), rot_z,
                   {"type": kind, "number": str(number), "zone": zone})


# --------------------------------------------------------------------------------------
# Furniture prototypes (local origin at ground centre, front = -Y)
# --------------------------------------------------------------------------------------
def f_lower_cabana(bm):
    bm_box(bm, (0, 0.4, 1.6), (4.2, 3.6, 3.2), "ivory")               # cabin
    bm_box(bm, (0, -1.6, 0.3), (3.8, 0.6, 0.6), "white")              # daybed front
    bm_box(bm, (0, -1.6, 0.7), (3.6, 0.5, 0.2), "orange")
    bm_box(bm, (0, -1.9, 3.0), (4.4, 2.2, 0.15), "red")               # awning
    bm_box(bm, (0, -1.9, 2.82), (4.4, 2.2, 0.2), "white")             # scalloped valance


def f_upper_cabana(bm):
    bm_box(bm, (0, 0.3, 1.6), (4.2, 3.4, 3.2), "ivory")
    bm_box(bm, (0, -1.8, 0.5), (4.2, 0.8, 1.0), "marble")             # balcony rail
    bm_box(bm, (0, -1.8, 2.9), (4.4, 2.0, 0.15), "red")
    bm_box(bm, (0, -1.8, 2.72), (4.4, 2.0, 0.2), "white")


def f_bungalow(bm):
    bm_box(bm, (0, 1.0, 1.9), (6.0, 4.6, 3.8), "ivory")
    bm_box(bm, (0, -2.2, 0.08), (6.0, 2.2, 0.16), "teal")             # private deck
    bm_box(bm, (1.6, -2.3, 0.0), (2.2, 1.6, 0.12), "water")           # plunge pool
    bm_box(bm, (-1.4, -2.3, 0.35), (1.8, 0.8, 0.5), "white")
    bm_box(bm, (-1.4, -2.3, 0.7), (1.7, 0.7, 0.2), "orange")
    bm_box(bm, (0, -2.6, 3.4), (6.2, 2.4, 0.15), "red")
    bm_box(bm, (0, -2.6, 3.22), (6.2, 2.4, 0.2), "white")


def f_daybed(bm):
    bm_box(bm, (0, 0, 0.25), (2.2, 2.2, 0.5), "white")
    bm_box(bm, (0, 0, 0.6), (2.0, 2.0, 0.2), "orange")
    bm_box(bm, (0, 0.95, 0.9), (2.0, 0.3, 0.8), "white")              # headboard
    bm_cyl(bm, (0, 0, 1.6), 0.05, 2.3, "white", 6)                    # umbrella pole
    bm_cyl(bm, (0, 0, 2.75), 1.25, 0.5, "red", 12, radius2=0.05, color_alt="white")


def f_lily_pad(bm):
    bm_cyl(bm, (0, 0, 0.25), 1.6, 0.5, "white", 16)
    bm_cyl(bm, (0, 0, 0.58), 1.45, 0.18, "orange", 16)


def f_beach_couch(bm):
    bm_box(bm, (0, 0, 0.25), (2.4, 1.3, 0.5), "white")
    bm_box(bm, (0, 0, 0.58), (2.2, 1.1, 0.18), "orange")
    bm_box(bm, (0, 0.5, 0.75), (2.4, 0.3, 0.5), "white")              # back


def f_casa_playa(bm):
    bm_box(bm, (0, 0, 0.25), (2.2, 2.0, 0.5), "white")
    bm_box(bm, (0, 0, 0.58), (2.0, 1.8, 0.18), "pink")
    bm_box(bm, (0, 0.85, 0.85), (2.0, 0.3, 0.7), "white")


def f_water_couch(bm):
    bm_box(bm, (0, 0, 0.05), (2.8, 2.8, 0.7), "white")                # in-pool platform
    bm_box(bm, (0, 0, 0.5), (2.6, 2.6, 0.2), "orange")
    bm_box(bm, (0, 1.2, 0.85), (2.6, 0.3, 0.5), "white")


def f_l_couch(bm):
    bm_box(bm, (0, 0, 0.3), (2.6, 0.9, 0.6), "white")
    bm_box(bm, (0.85, -0.85, 0.3), (0.9, 2.6, 0.6), "white")
    bm_box(bm, (0, 0, 0.66), (2.4, 0.8, 0.16), "purple")
    bm_box(bm, (0.85, -0.85, 0.66), (0.8, 2.4, 0.16), "purple")
    bm_box(bm, (-0.5, -1.0, 0.4), (1.2, 0.8, 0.8), "dark")            # table


def f_stage_couch(bm):
    bm_box(bm, (0, 0, 0.3), (2.6, 1.2, 0.6), "white")
    bm_box(bm, (0, 0, 0.66), (2.4, 1.0, 0.16), "orange")


def f_dance_couch_large(bm):
    bm_box(bm, (0, 0, 0.3), (3.0, 1.2, 0.6), "white")
    bm_box(bm, (0, 0, 0.66), (2.8, 1.0, 0.16), "green")


def f_dance_couch_medium(bm):
    bm_box(bm, (0, 0, 0.3), (2.2, 1.2, 0.6), "white")
    bm_box(bm, (0, 0, 0.66), (2.0, 1.0, 0.16), "pink")


def f_dance_couch_small(bm):
    bm_box(bm, (0, 0, 0.3), (1.6, 1.2, 0.6), "white")
    bm_box(bm, (0, 0, 0.66), (1.4, 1.0, 0.16), "blue")


def f_gaming_table(bm):
    bm_box(bm, (0, 0, 0.45), (1.6, 1.0, 0.9), "dark")
    bm_box(bm, (0, 0, 0.93), (1.7, 1.1, 0.06), "green")


def f_palm(bm):
    h = 11.0
    photo = LOOK == "photo"
    bm_cyl(bm, (0, 0, h / 2), 0.38, h, "trunk", 12 if photo else 6, radius2=0.22)
    uv = bm.loops.layers.uv.verify()
    u, v = palette_uv(CI["green"])
    n_fronds = 13 if photo else 7
    for i in range(n_fronds):
        a = math.radians(i * 360.0 / n_fronds + (i % 2) * 11)
        droop = 0.0 if not photo else (-0.9 if i % 3 == 0 else 0.3)
        d = Vector((math.cos(a), math.sin(a), 0))
        s = Vector((-math.sin(a), math.cos(a), 0))
        # polyline of the frond spine: 3 segments in photo mode
        pts = [Vector((0, 0, h))]
        if photo:
            pts += [pts[0] + d * 1.3 + Vector((0, 0, 0.6 + droop * 0.2)),
                    pts[0] + d * 2.6 + Vector((0, 0, 0.3 + droop * 0.8)),
                    pts[0] + d * 3.8 + Vector((0, 0, -1.2 + droop * 1.4))]
            widths = [0.25, 0.75, 0.6, 0.12]
        else:
            pts += [pts[0] + d * 1.7 + Vector((0, 0, 0.5)), pts[0] + d * 3.3 + Vector((0, 0, -1.3))]
            widths = [0.3, 0.7, 0.15]
        ring = []
        for pt, w in zip(pts, widths):
            ring.append((bm.verts.new(pt + s * w), bm.verts.new(pt - s * w)))
        for (a0, b0), (a1, b1) in zip(ring, ring[1:]):
            f = bm.faces.new((b0, a0, a1, b1))
            f.material_index = CI["green"]
            for loop in f.loops:
                loop[uv].uv = (u, v)
    bm_cyl(bm, (0, 0, h), 0.7, 1.0, "green", 6)


def f_umbrella(bm):
    bm_cyl(bm, (0, 0, 1.3), 0.05, 2.6, "white", 6)
    bm_cyl(bm, (0, 0, 2.75), 1.5, 0.5, "red", 12, radius2=0.05, color_alt="white")


FURNITURE = {
    "lower_cabana": f_lower_cabana,
    "upper_cabana": f_upper_cabana,
    "bungalow": f_bungalow,
    "daybed": f_daybed,
    "lily_pad": f_lily_pad,
    "beach_couch": f_beach_couch,
    "casa_playa": f_casa_playa,
    "water_couch": f_water_couch,
    "l_couch": f_l_couch,
    "stage_couch": f_stage_couch,
    "dance_couch_large": f_dance_couch_large,
    "dance_couch_medium": f_dance_couch_medium,
    "dance_couch_small": f_dance_couch_small,
    "gaming_table": f_gaming_table,
    "palm": f_palm,
    "umbrella": f_umbrella,
}

# --------------------------------------------------------------------------------------
# Layout data (plan pixels).  South side listed; north side is mirrored where noted.
# --------------------------------------------------------------------------------------
# Centre pool outline (clockwise on the plan).  South lobe + centre basin + north lobe.
POOL_PX = [
    (222, 318), (262, 310), (300, 312), (330, 320), (345, 342), (360, 350), (440, 350),
    (455, 342), (470, 320), (500, 312), (538, 310), (578, 318), (592, 345), (585, 380),
    (560, 405), (522, 425), (480, 418), (458, 392), (448, 420), (432, 438), (368, 438),
    (352, 420), (342, 392), (320, 418), (278, 425), (240, 405), (215, 380), (208, 345),
]

# Outdoor deck boundary
DECK_PX = [(24, 300), (60, 185), (130, 115), (250, 80), (550, 80), (670, 115), (740, 185),
           (776, 300), (776, 440), (720, 500), (620, 560), (500, 585), (300, 585),
           (180, 560), (80, 500), (24, 440)]

# Lily pads: south lobe 400-409 along an arc, north lobe 410-423
LILY_SOUTH_ARC = ((268, 368), 48.0, 95.0, -100.0)   # (centre px, radius px, start deg, end deg)
LILY_NORTH_ARC = ((532, 368), 48.0, 85.0, -100.0)

DAYBEDS_SOUTH = {300: (158, 322), 301: (178, 322), 302: (180, 285), 303: (198, 285),
                 304: (216, 285), 305: (214, 318)}
DAYBEDS_NORTH = {306: (586, 318), 307: (584, 285), 308: (602, 285), 309: (620, 285),
                 310: (622, 322), 311: (642, 322)}

CASA_PLAYA = {  # salmon "Casa Playa" legend entry: beds in front of the gaming pavilion
700: (245, 290), 701: (268, 290), 702: (290, 290),
                703: (322, 325), 704: (340, 328), 705: (358, 328), 706: (376, 328),
                707: (424, 328), 708: (442, 328), 709: (460, 328),
                710: (510, 290), 711: (532, 290), 712: (555, 290),
                713: (302, 268), 714: (318, 268), 715: (482, 268), 716: (498, 268)}

WATER_COUCHES = {600: (368, 362), 601: (388, 362), 602: (418, 372), 603: (442, 372),
                 604: (400, 420), 605: (348, 380)}

BEACH_COUCHES = {}
for i, n in enumerate(range(500, 504)):
    BEACH_COUCHES[n] = (578 + i * 24, 393, 0)
for i, n in enumerate(range(504, 508)):
    BEACH_COUCHES[n] = (578 + i * 24, 420, 0)
BEACH_COUCHES[508] = (505, 437, 0)
for i, n in enumerate(range(509, 514)):
    BEACH_COUCHES[n] = (482 + i * 16, 462, 0)
for i, n in enumerate(range(514, 519)):
    BEACH_COUCHES[n] = (482 + i * 16, 492, 0)
for i, n in enumerate(range(519, 524)):
    BEACH_COUCHES[n] = (234 + i * 23, 443, 0)
for i, n in enumerate(range(524, 529)):
    BEACH_COUCHES[n] = (234 + i * 23, 468, 0)
BEACH_COUCHES[529] = (205, 468, 0)
BEACH_COUCHES[530] = (198, 327, 90)
BEACH_COUCHES[531] = (198, 350, 90)
BEACH_COUCHES[532] = (590, 327, 90)
BEACH_COUCHES[533] = (590, 350, 90)
BEACH_COUCHES[534] = (612, 340, 90)
BEACH_COUCHES[800] = (352, 516, 0)
for i, n in enumerate(range(801, 809)):
    BEACH_COUCHES[n] = (328 - i * 17.5, 516, 0)
BEACH_COUCHES[809] = (176, 505, 90)
BEACH_COUCHES[810] = (176, 483, 90)
BEACH_COUCHES[811] = (212, 457, 0)
BEACH_COUCHES[812] = (236, 457, 0)
for i, n in enumerate(range(813, 819)):
    BEACH_COUCHES[n] = (328 - i * 17.5, 492, 0)
BEACH_COUCHES[819] = (222, 492, 0)
BEACH_COUCHES[820] = (204, 492, 0)
BEACH_COUCHES[821] = (258, 457, 0)

L_COUCHES = {30: (380, 512, 0), 31: (380, 492, 0), 32: (380, 472, 0), 33: (380, 452, 0),
             34: (438, 472, 90), 35: (438, 492, 90), 36: (438, 512, 90)}

STAGE_COUCHES = {98: (366, 580), 99: (436, 580)}

BACKSTAGE = {  # number: (px, py, kind)
    8: (352, 598, "dance_couch_large"), 7: (352, 648, "dance_couch_large"),
    6: (352, 698, "dance_couch_large"), 5: (352, 748, "dance_couch_medium"),
    4: (352, 775, "dance_couch_small"), 3: (352, 800, "dance_couch_large"),
    2: (405, 793, "dance_couch_medium"), 1: (450, 800, "dance_couch_small"),
    12: (390, 598, "dance_couch_medium"), 13: (416, 598, "dance_couch_medium"),
    21: (390, 648, "dance_couch_medium"), 11: (416, 648, "dance_couch_medium"),
    20: (390, 698, "dance_couch_medium"), 10: (416, 698, "dance_couch_medium"),
    9: (416, 748, "dance_couch_medium"), 22: (390, 748, "dance_couch_medium"),
}

# Buildings / fixed structures: name -> (centre px, w px, h px, height m, colour, collection)
STRUCTURES = {
    "GamingBar":        ((400, 196), 150, 22, 1.1, "dark", "Bars"),
    "GamingBar_Front":  ((400, 196), 154, 26, 0.9, "marble", "Bars"),
    "PoolBar":          ((172, 410), 70, 18, 1.1, "dark", "Bars"),
    "PoolBar_Front":    ((172, 410), 74, 22, 0.9, "marble", "Bars"),
    "UltraBar":         ((690, 395), 22, 60, 1.1, "dark", "Bars"),
    "UltraBar_Front":   ((690, 395), 26, 64, 0.9, "marble", "Bars"),
    "Grill":            ((586, 440), 40, 22, 1.1, "dark", "Bars"),
    "Grill_Front":      ((586, 440), 44, 26, 0.9, "marble", "Bars"),
    "ServiceBar":       ((508, 510), 40, 14, 1.1, "dark", "Bars"),
    "Restrooms_South":  ((120, 410), 50, 16, 3.2, "ivory", "Walkways"),
    "Restrooms_North":  ((690, 370), 50, 16, 3.2, "ivory", "Walkways"),
    "OutsideEntrance":  ((108, 376), 44, 18, 3.6, "ivory", "Walkways"),
    "Lockers":          ((108, 340), 36, 30, 3.2, "ivory", "Walkways"),
    "Stage":            ((400, 553), 82, 32, 1.2, "dark", "Stage"),
    "Stage_Riser":      ((400, 568), 100, 10, 0.5, "dark", "Stage"),
    "DJ_Booth":         ((400, 548), 16, 10, 1.0, "white", "Stage"),
}

# Walkway strips (centre px, w, h)
WALKWAYS = [((400, 300), 180, 16), ((400, 462), 120, 60), ((190, 380), 30, 60),
            ((610, 380), 30, 60), ((400, 530), 160, 14), ((140, 255), 50, 110),
            ((660, 255), 50, 110), ((400, 170), 230, 16)]

# Palms: (px, py)
PALMS = [(150, 345), (650, 345), (110, 450), (690, 450), (150, 505), (650, 505),
         (215, 555), (585, 555), (295, 300), (505, 300), (292, 560), (508, 560),
         (60, 300), (740, 300), (222, 270), (578, 270), (105, 270), (695, 270),
         (660, 410), (165, 385)]

# Umbrellas on open deck: (px, py)
UMBRELLAS = [(300, 460), (500, 455), (260, 300), (540, 300), (180, 300), (620, 300),
             (210, 380), (590, 380), (150, 390), (650, 390)]


# --------------------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------------------
def cut_hole(ob, poly, z0, z1):
    """Boolean-subtract an extruded polygon from a mesh object (modifier applied immediately)."""
    bm = bmesh.new()
    bm_prism(bm, poly, z0, z1, "water")
    cutter = add_obj("Cutter", finish_mesh("M_Cutter", bm), "Pools")
    mod = ob.modifiers.new("cut", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.object = cutter
    mod.solver = "EXACT"
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    new_mesh = bpy.data.meshes.new_from_object(ev)
    mats = list(ob.data.materials)
    new_mesh.materials.clear()
    for m in mats:
        new_mesh.materials.append(m)
    ob.modifiers.clear()
    old = ob.data
    ob.data = new_mesh
    bpy.data.meshes.remove(old)
    cm = cutter.data
    bpy.data.objects.remove(cutter)
    bpy.data.meshes.remove(cm)
    STATS["objects"] -= 1


def build_ground():
    bm = bmesh.new()
    deck = [P(x, y) for x, y in DECK_PX]
    bm_prism(bm, deck, -0.3, 0.0, "deck")
    mesh = finish_mesh("M_Deck", bm)
    ob = add_obj("Deck", mesh, "Walkways")

    cut_hole(ob, [P(x, y) for x, y in POOL_PX], -3.0, 1.0)

    # Backstage ground slab (the corridor sits on its own footprint)
    bm = bmesh.new()
    bm_prism(bm, rect_px(402, 705, 150, 270), -0.3, 0.0, "deck")
    add_obj("Backstage_Floor", finish_mesh("M_BackstageFloor", bm), "Backstage")

    # Crescent terrace (lower cabana level) and upper terrace slab
    bm = bmesh.new()
    bm_prism(bm, ring_poly(300, 372, 104, 151, 10), -0.3, 0.05, "deck")
    bm_prism(bm, ring_poly(300, 372, 29, 76, 10), -0.3, 0.05, "deck")
    add_obj("Terrace_Lower", finish_mesh("M_TerraceLower", bm), "Walkways")

    bm = bmesh.new()
    bm_prism(bm, ring_poly(372, 445, 102, 152, 12), 3.5, 3.9, "ivory")
    bm_prism(bm, ring_poly(372, 445, 28, 78, 12), 3.5, 3.9, "ivory")
    bm_prism(bm, ring_poly(370, 374, 104, 151, 12), 3.9, 4.9, "marble")   # balustrade
    bm_prism(bm, ring_poly(370, 374, 29, 76, 12), 3.9, 4.9, "marble")
    add_obj("Terrace_Upper", finish_mesh("M_TerraceUpper", bm), "Cabanas_Upper")

    # Back facade wall (ivory classical)
    bm = bmesh.new()
    bm_prism(bm, ring_poly(445, 452, 100, 153, 14), 0.0, 8.5, "ivory")
    bm_prism(bm, ring_poly(445, 452, 27, 80, 14), 0.0, 8.5, "ivory")
    bm_prism(bm, ring_poly(445, 455, 100, 153, 14), 8.5, 9.0, "white")    # cornice
    bm_prism(bm, ring_poly(445, 455, 27, 80, 14), 8.5, 9.0, "white")
    add_obj("Facade", finish_mesh("M_Facade", bm), "Cabanas_Upper")


def build_pool():
    bm = bmesh.new()
    poly = [P(x, y) for x, y in POOL_PX]
    bm_prism(bm, poly, -1.6, 0.0, "water" if LOOK == "photo" else "marble", top=False)   # basin walls + floor
    add_obj("Pool_Basin", finish_mesh("M_PoolBasin", bm), "Pools")

    bm = bmesh.new()
    bm_face(bm, poly, -0.15, "water")
    add_obj("Pool_Water", finish_mesh("M_PoolWater", bm, "Water"), "Pools")

    # coping ring: thin outline slab slightly above deck
    bm = bmesh.new()
    n = len(poly)
    cx = sum(p[0] for p in poly) / n
    cy = sum(p[1] for p in poly) / n
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        bm_box(bm, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.04),
               (math.dist(a, b) + 0.2, 0.6, 0.08), "white",
               math.atan2(b[1] - a[1], b[0] - a[0]))
    add_obj("Pool_Coping", finish_mesh("M_PoolCoping", bm), "Pools")


def build_gaming_pavilion():
    bm = bmesh.new()
    bm_prism(bm, rect_px(400, 222, 210, 90), 7.0, 7.6, "ivory")           # roof
    bm_prism(bm, rect_px(400, 222, 214, 94), 6.6, 7.0, "white")           # cornice
    add_obj("Gaming_Roof", finish_mesh("M_GamingRoof", bm), "Gaming")
    bm = bmesh.new()
    bm_prism(bm, rect_px(400, 180, 210, 8), 0.0, 7.0, "ivory")            # back wall
    for px in (300, 335, 370, 430, 465, 500):                             # columns
        x, y = P(px, 266)
        segs = 24 if LOOK == "photo" else 8
        bm_cyl(bm, (x, y, 3.5), 0.45, 7.0, "white", segs)
        if LOOK == "photo":
            bm_box(bm, (x, y, 0.2), (1.3, 1.3, 0.4), "white")                 # base
            bm_box(bm, (x, y, 6.75), (1.3, 1.3, 0.5), "white")                # capital
    bm_prism(bm, rect_px(400, 222, 210, 90), -0.05, 0.02, "marble")       # floor
    add_obj("Gaming_Pavilion", finish_mesh("M_GamingPavilion", bm), "Gaming")


def build_structures():
    for name, (c, w, h, hz, color, zone) in STRUCTURES.items():
        bm = bmesh.new()
        bm_prism(bm, rect_px(c[0], c[1], w, h), 0.0, hz, color)
        add_obj(name, finish_mesh("M_" + name, bm), zone)
    # Stage roof / truss
    bm = bmesh.new()
    bm_prism(bm, rect_px(400, 556, 96, 40), 8.0, 8.6, "dark")
    for px, py in ((356, 538), (444, 538), (356, 574), (444, 574)):
        x, y = P(px, py)
        bm_cyl(bm, (x, y, 4.0), 0.25, 8.0, "dark", 6)
    add_obj("Stage_Truss", finish_mesh("M_StageTruss", bm), "Stage")


def build_backstage():
    bm = bmesh.new()
    for a, b in ((572, 840), ):
        bm_prism(bm, rect_px(329, (a + b) / 2, 4, b - a), 0.0, 6.0, "ivory")   # west wall
        bm_prism(bm, rect_px(475, (a + b) / 2, 4, b - a), 0.0, 6.0, "ivory")   # east wall
        bm_prism(bm, rect_px(402, b, 150, 4), 0.0, 6.0, "ivory")              # far end wall
    bm_prism(bm, rect_px(360, 572, 60, 4), 0.0, 6.0, "ivory")           # stage-side wall, doorway in the middle
    bm_prism(bm, rect_px(444, 572, 60, 4), 0.0, 6.0, "ivory")
    bm_prism(bm, rect_px(492, 800, 36, 30), 0.0, 4.0, "ivory")          # inside entrance lobby
    bm_prism(bm, rect_px(476, 775, 30, 20), 0.0, 4.0, "ivory")          # restrooms
    add_obj("Backstage_Walls", finish_mesh("M_BackstageWalls", bm), "Backstage")

    bm = bmesh.new()
    bm_prism(bm, rect_px(402, 705, 150, 270), 6.0, 6.5, "ivory")
    add_obj("Backstage_Roof", finish_mesh("M_BackstageRoof", bm), "Backstage")

    bm = bmesh.new()
    bm_prism(bm, rect_px(446, 680, 14, 170), 0.0, 1.1, "dark")           # long bar
    bm_prism(bm, rect_px(446, 680, 18, 174), 0.0, 0.9, "marble")
    bm_prism(bm, rect_px(352, 700, 14, 220), 0.0, 0.12, "green")         # dance floor strip
    bm_prism(bm, rect_px(403, 670, 44, 180), 0.0, 0.12, "pink")
    add_obj("Backstage_Bar", finish_mesh("M_BackstageBar", bm), "Bars")


def build_walkways():
    bm = bmesh.new()
    for c, w, h in WALKWAYS:
        bm_prism(bm, rect_px(c[0], c[1], w, h), 0.0, 0.03, "walk")
    add_obj("Walkway_Paths", finish_mesh("M_Walkways", bm), "Walkways")


def build_cabanas():
    # lower cabanas 100-106 (south) and 107-113 (north)
    south = [(100 + i, 150.0 - i * 4.1) for i in range(7)]
    for n, a in south:
        x, y = arc_xy(R_LOWER_PX, a)
        table(n, "lower_cabana", "Cabanas_Lower", (x, y), 0.0, arc_rot(a))
        x, y = arc_xy(R_LOWER_PX, 180 - a)
        table(n + 7, "lower_cabana", "Cabanas_Lower", (x, y), 0.0, arc_rot(180 - a))
    # upper cabanas 200-206 (south) and 207-213 (north)
    south = [(200 + i, 149.5 - i * 4.4) for i in range(7)]
    for n, a in south:
        x, y = arc_xy(R_UPPER_PX, a)
        table(n, "upper_cabana", "Cabanas_Upper", (x, y), 3.9, arc_rot(a))
        x, y = arc_xy(R_UPPER_PX, 180 - a)
        table(n + 7, "upper_cabana", "Cabanas_Upper", (x, y), 3.9, arc_rot(180 - a))
    # bungalows: lower 1-4, upper 5-8
    for n, a in ((1, 121.0), (2, 113.5)):
        x, y = arc_xy(R_LOWER_BUNG_PX, a)
        table(n, "bungalow", "Bungalows", (x, y), 0.0, arc_rot(a))
        x, y = arc_xy(R_LOWER_BUNG_PX, 180 - a)
        table(n + 2, "bungalow", "Bungalows", (x, y), 0.0, arc_rot(180 - a))
    for n, a in ((5, 118.0), (6, 110.5)):
        x, y = arc_xy(R_UPPER_BUNG_PX, a)
        table(n, "bungalow", "Bungalows", (x, y), 3.9, arc_rot(a))
        x, y = arc_xy(R_UPPER_BUNG_PX, 180 - a)
        table(n + 2, "bungalow", "Bungalows", (x, y), 3.9, arc_rot(180 - a))


def build_pool_furniture():
    (cx, cy), r, a0, a1 = LILY_SOUTH_ARC
    for i in range(10):
        a = math.radians(a0 + (a1 - a0) * i / 9)
        table(400 + i, "lily_pad", "Pools", P(cx + r * math.cos(a), cy - r * math.sin(a)), -0.15)
    (cx, cy), r, a0, a1 = LILY_NORTH_ARC
    for i in range(14):
        a = math.radians(a0 + (a1 - a0) * i / 13)
        table(410 + i, "lily_pad", "Pools", P(cx + r * math.cos(a), cy - r * math.sin(a)), -0.15)
    for n, (px, py) in WATER_COUCHES.items():
        table(n, "water_couch", "Pools", P(px, py), -0.15)


def build_deck_furniture():
    for n, (px, py) in {**DAYBEDS_SOUTH, **DAYBEDS_NORTH}.items():
        table(n, "daybed", "Daybeds", P(px, py))
    for n, (px, py) in CASA_PLAYA.items():
        table(n, "casa_playa", "Daybeds", P(px, py), 0.0, math.pi)
    for n, (px, py, rot) in BEACH_COUCHES.items():
        table(n, "beach_couch", "Beach_Couches", P(px, py), 0.0, math.radians(rot))
    for n, (px, py, rot) in L_COUCHES.items():
        table(n, "l_couch", "Dance_Floor", P(px, py), 0.0, math.radians(rot))
    for n, (px, py) in STAGE_COUCHES.items():
        table(n, "stage_couch", "Stage", P(px, py), 0.0, math.pi)
    for n, (px, py, kind) in BACKSTAGE.items():
        table(n, kind, "Backstage", P(px, py), 0.0, math.pi / 2 if px < 380 else 0.0)
    for i in range(8):
        table("G%d" % (i + 1), "gaming_table", "Gaming", P(345 + i * 15.7, 262), 0.0, math.pi)
    mesh = proto("palm", f_palm)
    for i, (px, py) in enumerate(PALMS):
        add_obj("Palm_%02d" % i, mesh, "Landscape", (*P(px, py), 0.0), math.radians((i * 47) % 360))
    mesh = proto("umbrella", f_umbrella)
    for i, (px, py) in enumerate(UMBRELLAS):
        add_obj("Umbrella_%02d" % i, mesh, "Landscape", (*P(px, py), 0.0))


def build_blockout():
    """Stage 1: flat zone slabs only, for comparing against the floor plan."""
    zones = [
        ("Zone_GamingPavilion", rect_px(400, 222, 210, 90), "dark"),
        ("Zone_Stage", rect_px(400, 556, 96, 40), "orange"),
        ("Zone_Backstage", rect_px(402, 705, 150, 270), "pink"),
        ("Zone_DanceFloor", rect_px(408, 485, 90, 70), "purple"),
        ("Zone_Cabanas_Lower_S", ring_poly(318, 366, 104, 152), "yellow"),
        ("Zone_Cabanas_Lower_N", ring_poly(318, 366, 28, 76), "yellow"),
        ("Zone_Cabanas_Upper_S", ring_poly(380, 440, 102, 154), "orange"),
        ("Zone_Cabanas_Upper_N", ring_poly(380, 440, 26, 78), "orange"),
        ("Zone_Bungalows_1_2", ring_poly(300, 355, 109, 125), "teal"),
        ("Zone_Bungalows_3_4", ring_poly(300, 355, 55, 71), "teal"),
        ("Zone_Bungalows_5_6", ring_poly(372, 420, 106, 122), "teal"),
        ("Zone_Bungalows_7_8", ring_poly(372, 420, 58, 74), "teal"),
        ("Zone_Daybeds_S", rect_px(188, 304, 70, 48), "red"),
        ("Zone_Daybeds_N", rect_px(612, 304, 70, 48), "red"),
        ("Zone_Couches_500N", rect_px(614, 406, 100, 44), "yellow"),
        ("Zone_Couches_510", rect_px(514, 466, 80, 60), "yellow"),
        ("Zone_Couches_520", rect_px(280, 456, 120, 40), "yellow"),
        ("Zone_Couches_800", rect_px(262, 500, 190, 44), "yellow"),
        ("Zone_PoolBar", rect_px(172, 410, 70, 18), "dark"),
        ("Zone_UltraBar", rect_px(690, 395, 22, 60), "dark"),
        ("Zone_Grill", rect_px(586, 440, 40, 22), "dark"),
    ]
    for name, poly, color in zones:
        bm = bmesh.new()
        bm_prism(bm, poly, 0.0, 0.4, color)
        add_obj(name, finish_mesh("M_" + name, bm), "Blockout")



# --------------------------------------------------------------------------------------
# Photo look: procedural node materials (no downloaded textures), extra detail, backdrop
# --------------------------------------------------------------------------------------
def _pm_base(name, color, rough=0.6, metal=0.0, spec=0.5, sheen=0.0, coat=0.0):
    m = bpy.data.materials.new("P_" + name)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1.0)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    b.inputs["Specular IOR Level"].default_value = spec
    b.inputs["Sheen Weight"].default_value = sheen
    b.inputs["Coat Weight"].default_value = coat
    coord = nt.nodes.new("ShaderNodeTexCoord")
    m["_coord"] = coord.name
    return m, nt, b, coord


def _noise(nt, coord, scale, detail=4.0, rough=0.5, out="Fac"):
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = scale
    n.inputs["Detail"].default_value = detail
    n.inputs["Roughness"].default_value = rough
    nt.links.new(coord.outputs["Object"], n.inputs["Vector"])
    return n.outputs[out]


def _bump(nt, b, height_socket, strength, distance=0.02):
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    bump.inputs["Distance"].default_value = distance
    nt.links.new(height_socket, bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return bump


def _mix(nt, fac_socket, c1, c2):
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*c1, 1.0)
    mix.inputs["B"].default_value = (*c2, 1.0)
    nt.links.new(fac_socket, mix.inputs["Factor"])
    return mix.outputs["Result"]


def _brick(nt, coord, scale, width, height, mortar=0.012, c1=(1, 1, 1), c2=(0.9, 0.9, 0.9), cm=(0.5, 0.5, 0.5)):
    br = nt.nodes.new("ShaderNodeTexBrick")
    br.inputs["Scale"].default_value = scale
    br.inputs["Mortar Size"].default_value = mortar
    br.inputs["Mortar Smooth"].default_value = 0.3
    br.inputs["Brick Width"].default_value = width
    br.inputs["Row Height"].default_value = height
    br.inputs["Color1"].default_value = (*c1, 1.0)
    br.inputs["Color2"].default_value = (*c2, 1.0)
    br.inputs["Mortar"].default_value = (*cm, 1.0)
    br.offset = 0.5
    nt.links.new(coord.outputs["Object"], br.inputs["Vector"])
    return br


def _mapped(nt, coord, mapping_scale):
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = mapping_scale
    nt.links.new(coord.outputs["Object"], mp.inputs["Vector"])
    return mp


def pm_stucco(name, color):
    m, nt, b, co = _pm_base(name, color, rough=0.78)
    var = _mix(nt, _noise(nt, co, 0.6, 3), color, tuple(c * 0.9 for c in color))
    nt.links.new(var, b.inputs["Base Color"])
    _bump(nt, b, _noise(nt, co, 45.0, 6, 0.6), 0.18, 0.01)
    return m


def pm_stone_tiles(name, c1, c2, cm, width, height, rough=0.45):
    m, nt, b, co = _pm_base(name, c1, rough=rough, spec=0.4)
    br = _brick(nt, co, 1.0, width, height, 0.01, c1, c2, cm)
    veins = _noise(nt, co, 1.2, 8, 0.7)
    col = _mix(nt, veins, c1, c2)
    tile = nt.nodes.new("ShaderNodeMix")
    tile.data_type = "RGBA"
    nt.links.new(br.outputs["Fac"], tile.inputs["Factor"])
    nt.links.new(col, tile.inputs["A"])
    tile.inputs["B"].default_value = (*cm, 1.0)
    nt.links.new(tile.outputs["Result"], b.inputs["Base Color"])
    rough_mix = _mix(nt, br.outputs["Fac"], (rough,) * 3, (0.9,) * 3)
    nt.links.new(rough_mix, b.inputs["Roughness"])
    # bump: grout lines + fine grain
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "MULTIPLY_ADD"
    nt.links.new(_noise(nt, co, 60.0, 4, 0.5), add.inputs[0])
    add.inputs[1].default_value = 0.15
    nt.links.new(br.outputs["Fac"], add.inputs[2])
    _bump(nt, b, add.outputs["Value"], 0.35, 0.01)
    return m


def pm_fabric(name, color, sheen=0.35, rough=0.92):
    m, nt, b, co = _pm_base(name, color, rough=rough, spec=0.3, sheen=sheen)
    var = _mix(nt, _noise(nt, co, 3.0, 2), color, tuple(min(1, c * 1.08) for c in color))
    nt.links.new(var, b.inputs["Base Color"])
    _bump(nt, b, _noise(nt, co, 300.0, 2, 0.5), 0.12, 0.003)
    return m


def pm_wicker(name, color):
    m, nt, b, co = _pm_base(name, color, rough=0.62, spec=0.45)
    w1 = nt.nodes.new("ShaderNodeTexWave")
    w1.wave_type = "BANDS"
    w1.bands_direction = "X"
    w1.inputs["Scale"].default_value = 70.0
    w1.inputs["Distortion"].default_value = 0.6
    w2 = nt.nodes.new("ShaderNodeTexWave")
    w2.wave_type = "BANDS"
    w2.bands_direction = "Y"
    w2.inputs["Scale"].default_value = 70.0
    w2.inputs["Distortion"].default_value = 0.6
    nt.links.new(co.outputs["Object"], w1.inputs["Vector"])
    nt.links.new(co.outputs["Object"], w2.inputs["Vector"])
    mul = nt.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    nt.links.new(w1.outputs["Fac"], mul.inputs[0])
    nt.links.new(w2.outputs["Fac"], mul.inputs[1])
    _bump(nt, b, mul.outputs["Value"], 0.45, 0.01)
    var = _mix(nt, _noise(nt, co, 8.0, 3), color, tuple(c * 0.88 for c in color))
    nt.links.new(var, b.inputs["Base Color"])
    return m


def pm_marble(name, c1=(0.92, 0.91, 0.88), c2=(0.55, 0.54, 0.52)):
    m, nt, b, co = _pm_base(name, c1, rough=0.18, spec=0.6, coat=0.3)
    veins = _noise(nt, co, 0.8, 10, 0.75)
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.42
    ramp.color_ramp.elements[0].color = (*c1, 1.0)
    ramp.color_ramp.elements[1].position = 0.6
    ramp.color_ramp.elements[1].color = (*c2, 1.0)
    e = ramp.color_ramp.elements.new(0.52)
    e.color = (*c1, 1.0)
    nt.links.new(veins, ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    _bump(nt, b, _noise(nt, co, 90.0, 3, 0.5), 0.05, 0.003)
    return m


def pm_bark(name, color):
    m, nt, b, co = _pm_base(name, color, rough=0.95, spec=0.2)
    mp = _mapped(nt, co, (1.0, 1.0, 0.15))
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = 12.0
    n.inputs["Detail"].default_value = 8.0
    nt.links.new(mp.outputs["Vector"], n.inputs["Vector"])
    var = _mix(nt, n.outputs["Fac"], tuple(c * 0.7 for c in color), tuple(min(1, c * 1.2) for c in color))
    nt.links.new(var, b.inputs["Base Color"])
    _bump(nt, b, n.outputs["Fac"], 0.8, 0.03)
    return m


def pm_leaf(name, color):
    m, nt, b, co = _pm_base(name, color, rough=0.45, spec=0.5, coat=0.15)
    var = _mix(nt, _noise(nt, co, 1.5, 3), color, (color[0] * 1.3, color[1] * 1.15, color[2] * 0.8))
    nt.links.new(var, b.inputs["Base Color"])
    b.inputs["Subsurface Weight"].default_value = 0.15
    b.inputs["Subsurface Radius"].default_value = (0.2, 0.4, 0.1)
    return m


def pm_metal(name, color, rough=0.35, metal=0.85):
    m, nt, b, co = _pm_base(name, color, rough=rough, metal=metal)
    _bump(nt, b, _noise(nt, co, 150.0, 2, 0.5), 0.05, 0.002)
    return m


def pm_painted_wood(name, color):
    m, nt, b, co = _pm_base(name, color, rough=0.5, spec=0.5, coat=0.2)
    mp = _mapped(nt, co, (1.0, 25.0, 1.0))
    n = nt.nodes.new("ShaderNodeTexNoise")
    n.inputs["Scale"].default_value = 4.0
    n.inputs["Detail"].default_value = 6.0
    nt.links.new(mp.outputs["Vector"], n.inputs["Vector"])
    _bump(nt, b, n.outputs["Fac"], 0.15, 0.005)
    return m


def pm_glass_bronze(name):
    m, nt, b, co = _pm_base(name, (0.55, 0.42, 0.28), rough=0.28, metal=0.75, spec=0.6)
    # floor bands: horizontal brick texture darkens every storey line
    br = _brick(nt, co, 1.0, 100.0, 3.6, 0.04, (0.62, 0.42, 0.2), (0.58, 0.40, 0.2), (0.12, 0.08, 0.05))
    mp = _mapped(nt, co, (1.0, 1.0, 1.0))
    nt.links.new(mp.outputs["Vector"], br.inputs["Vector"])
    nt.links.new(br.outputs["Color"], b.inputs["Base Color"])
    _bump(nt, b, br.outputs["Fac"], 0.3, 0.02)
    return m


def pm_water_photo():
    m = bpy.data.materials.new("P_Water")
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    out = nt.nodes["Material Output"]
    b.inputs["Base Color"].default_value = (0.85, 0.97, 1.0, 1.0)
    b.inputs["Roughness"].default_value = 0.07
    b.inputs["IOR"].default_value = 1.333
    b.inputs["Transmission Weight"].default_value = 1.0
    b.inputs["Specular IOR Level"].default_value = 0.5
    co = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1.0, 1.0, 1.0)
    nt.links.new(co.outputs["Object"], mp.inputs["Vector"])
    n1 = nt.nodes.new("ShaderNodeTexNoise")
    n1.inputs["Scale"].default_value = 2.5
    n1.inputs["Detail"].default_value = 5.0
    n1.inputs["Roughness"].default_value = 0.55
    nt.links.new(mp.outputs["Vector"], n1.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")          # gentle ripples; strong bump scatters tower reflections
    bump.inputs["Strength"].default_value = 0.05
    bump.inputs["Distance"].default_value = 0.01
    nt.links.new(n1.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Base Color"].default_value = (0.55, 0.92, 0.95, 1.0)
    b.inputs["Alpha"].default_value = 1.0
    # Let sunlight through to the pool floor: shadow rays see a transparent shader, so the
    # basin is lit directly even with refractive caustics disabled in the render settings.
    lp = nt.nodes.new("ShaderNodeLightPath")
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Shadow Ray"], mix.inputs["Fac"])
    nt.links.new(b.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tr.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return m


def make_photo_materials():
    """One procedural material per palette entry, in palette order (matches face material_index)."""
    global PHOTO_MATS
    specs = {
        "ivory":  lambda: pm_stucco("ivory", (0.90, 0.85, 0.74)),
        "deck":   lambda: pm_stone_tiles("deck", (0.82, 0.75, 0.62), (0.74, 0.66, 0.53), (0.60, 0.54, 0.44), 1.2, 0.6),
        "walk":   lambda: pm_stone_tiles("walk", (0.62, 0.59, 0.54), (0.54, 0.51, 0.46), (0.40, 0.38, 0.35), 0.6, 0.6, rough=0.55),
        "orange": lambda: pm_fabric("orange", (0.92, 0.38, 0.06)),
        "yellow": lambda: pm_fabric("yellow", (0.92, 0.74, 0.18)),
        "red":    lambda: pm_fabric("red", (0.72, 0.06, 0.05), sheen=0.15, rough=0.8),
        "white":  lambda: pm_wicker("white", (0.93, 0.92, 0.88)),
        "water":  lambda: pm_stucco("plaster", (0.30, 0.68, 0.74)),
        "green":  lambda: pm_leaf("leaf", (0.12, 0.38, 0.14)),
        "trunk":  lambda: pm_bark("bark", (0.40, 0.29, 0.18)),
        "purple": lambda: pm_fabric("purple", (0.42, 0.18, 0.58), sheen=0.6, rough=0.85),
        "pink":   lambda: pm_fabric("pink", (0.85, 0.35, 0.50), sheen=0.6, rough=0.85),
        "blue":   lambda: pm_fabric("blue", (0.12, 0.35, 0.80), sheen=0.6, rough=0.85),
        "dark":   lambda: pm_metal("dark", (0.05, 0.05, 0.06), rough=0.4, metal=0.7),
        "teal":   lambda: pm_painted_wood("teal", (0.40, 0.70, 0.68)),
        "marble": lambda: pm_marble("marble"),
    }
    PHOTO_MATS = [specs[name]() for name, _ in PALETTE]
    MATS["Water"] = pm_water_photo()
    MATS["Glass_Bronze"] = pm_glass_bronze("glass_bronze")
    MATS["Asphalt"] = pm_stone_tiles("asphalt", (0.22, 0.22, 0.22), (0.20, 0.20, 0.21), (0.18, 0.18, 0.18), 40.0, 40.0, rough=0.9)
    MATS["Sand"] = pm_stucco("sand", (0.70, 0.62, 0.48))


def build_photo_details():
    """Balustrade posts, cabana awning scallops, umbrellas with 16 segments: photo mode only."""
    bm = bmesh.new()
    for a0, a1 in ((104.0, 151.0), (29.0, 76.0)):
        n = 40
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            x, y = arc_xy(371.5, a)
            bm_cyl(bm, (x, y, 4.35), 0.06, 0.9, "white", 8)
    add_obj("Balustrade_Posts", finish_mesh("M_BalustradePosts", bm), "Cabanas_Upper")

    # scallop drops under every cabana / bungalow awning edge
    bm = bmesh.new()
    for ob in list(bpy.data.objects):
        if not ob.name.startswith("T_"):
            continue
        kind = ob.get("type")
        if kind not in ("lower_cabana", "upper_cabana", "bungalow"):
            continue
        width = 6.2 if kind == "bungalow" else 4.4
        front = -3.8 if kind == "bungalow" else (-3.0 if kind == "lower_cabana" else -2.8)
        zz = (3.1 if kind == "bungalow" else (2.7 if kind == "lower_cabana" else 2.6)) + ob.location.z
        count = int(width / 0.45)
        for i in range(count):
            lx = -width / 2 + (i + 0.5) * width / count
            local = Vector((lx, front, 0))
            local.rotate(ob.rotation_euler)
            wx, wy = ob.location.x + local.x, ob.location.y + local.y
            bm_cyl(bm, (wx, wy, zz), 0.22, 0.25, "red" if i % 2 == 0 else "white", 8)
    add_obj("Awning_Scallops", finish_mesh("M_AwningScallops", bm), "Cabanas_Lower")


def build_context():
    """Surroundings for the photo renders: ground, the Encore tower and the casino low-rise."""
    bm = bmesh.new()
    bm_prism(bm, [(-400, -400), (400, -400), (400, 400), (-400, 400)], -0.6, -0.35, "deck")
    m = finish_mesh("M_Ground", bm, "Asphalt")
    ground = add_obj("Ground", m, "Context")
    cut_hole(ground, [P(x, y) for x, y in POOL_PX], -3.0, 1.0)

    # Encore tower: curved bronze-glass slab behind the crescent (height and radius are estimates)
    bm = bmesh.new()
    bm_prism(bm, ring_poly(560, 598, 56, 124, 24), -0.3, 150.0, "ivory")
    bm_prism(bm, ring_poly(556, 602, 56, 124, 24), 150.0, 153.0, "ivory")  # crown
    m = finish_mesh("M_EncoreTower", bm, "Glass_Bronze")
    add_obj("Encore_Tower", m, "Context")

    # Encore casino low-rise where the backstage corridor leads, plus a podium behind the tower
    bm = bmesh.new()
    bm_prism(bm, [P(120, 860), P(680, 860), P(680, 1180), P(120, 1180)], -0.3, 15.0, "ivory")
    bm_prism(bm, [P(120, 860), P(680, 860), P(680, 1180), P(120, 1180)], 15.0, 16.0, "white")
    bm_prism(bm, ring_poly(452, 470, 40, 140, 24), -0.3, 12.0, "ivory")
    add_obj("Encore_Casino", finish_mesh("M_EncoreCasino", bm), "Context")

    # Extra palms outside the deck for depth
    mesh = proto("palm", f_palm)
    pts = [(-70, -10), (-66, 8), (-60, 24), (70, -10), (66, 8), (60, 24), (-30, -60), (30, -60),
           (-54, -40), (54, -40), (-45, 42), (45, 42)]
    for i, (x, y) in enumerate(pts):
        add_obj("PalmCtx_%02d" % i, mesh, "Context", (x, y, -0.3), math.radians((i * 71) % 360))



# --------------------------------------------------------------------------------------
# Baking: procedural materials -> image textures, so the photo look survives glTF export
# --------------------------------------------------------------------------------------
BAKE_SKIP = {"M_PoolWater", "M_Ground", "M_Cutter"}


def _bake_res_for(mesh, base):
    xs = [v.co.x for v in mesh.vertices]
    ys = [v.co.y for v in mesh.vertices]
    zs = [v.co.z for v in mesh.vertices]
    if not xs:
        return base // 2
    extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    if extent > 60:
        return base * 4
    if extent > 20:
        return base * 2
    if extent > 6:
        return base
    return base // 2


def _unwrap(ob):
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _new_image(name, res, out_dir, color=True):
    img = bpy.data.images.new(name, res, res, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "sRGB" if color else "Non-Color"
    img.filepath_raw = os.path.join(out_dir, name + ".png")
    img.file_format = "PNG"
    return img


def _bake_pass(ob, mesh, img, bake_type, **kw):
    """Point every material slot's active image node at `img`, then bake."""
    nodes = []
    for mat in mesh.materials:
        if mat is None or not mat.use_nodes:
            continue
        nt = mat.node_tree
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        nt.nodes.active = tex
        nodes.append((nt, tex))
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.bake(type=bake_type, margin=8, use_clear=True, use_selected_to_active=False, **kw)
    for nt, tex in nodes:
        nt.nodes.remove(tex)


def bake_all(out_dir, base_res):
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.cycles.use_denoising = False
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    scene.render.bake.normal_space = "TANGENT"
    scene.render.bake.margin = 8

    # one representative object per mesh
    rep_obj = {}
    for ob in bpy.data.objects:
        if ob.type == "MESH" and ob.data.name not in rep_obj and ob.data.name not in BAKE_SKIP:
            rep_obj[ob.data.name] = ob
    hidden = []
    for ob in bpy.data.objects:   # baking ignores hidden objects, but keep roofs renderable
        if ob.hide_render:
            hidden.append(ob)
            ob.hide_render = False

    t0 = time.time()
    n = 0
    for mesh_name, ob in sorted(rep_obj.items()):
        mesh = ob.data
        if not mesh.polygons:
            continue
        res = _bake_res_for(mesh, base_res)
        _unwrap(ob)
        tag = mesh_name[2:] if mesh_name.startswith("M_") else mesh_name
        col = _new_image("bk_%s_col" % tag, res, out_dir, color=True)
        nrm = _new_image("bk_%s_nrm" % tag, res, out_dir, color=False)
        rgh = _new_image("bk_%s_rgh" % tag, res, out_dir, color=False)
        _bake_pass(ob, mesh, col, "DIFFUSE", pass_filter={"COLOR"})
        _bake_pass(ob, mesh, nrm, "NORMAL", normal_space="TANGENT")
        _bake_pass(ob, mesh, rgh, "ROUGHNESS")
        for img in (col, nrm, rgh):
            img.save()
            img.pack()
        metallic = 0.75 if mesh_name == "M_EncoreTower" else 0.0
        mesh.materials.clear()
        mesh.materials.append(_baked_material(tag, col, nrm, rgh, metallic))
        for poly in mesh.polygons:
            poly.material_index = 0
        n += 1
        print("[bake] %-24s %4dpx  (%.0fs)" % (mesh_name, res, time.time() - t0))
    for ob in hidden:
        ob.hide_render = True

    # export-friendly water (glTF cannot carry the Light Path trick)
    water = bpy.data.materials.new("Water_Export")
    water.use_nodes = True
    b = water.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.20, 0.72, 0.80, 1.0)
    b.inputs["Roughness"].default_value = 0.05
    b.inputs["Alpha"].default_value = 0.65
    water.blend_method = "BLEND"
    if "M_PoolWater" in bpy.data.meshes:
        wm = bpy.data.meshes["M_PoolWater"]
        wm.materials.clear()
        wm.materials.append(water)
    if "M_Ground" in bpy.data.meshes:
        gm = bpy.data.meshes["M_Ground"]
        g = bpy.data.materials.new("Ground_Export")
        g.use_nodes = True
        g.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.24, 0.24, 0.24, 1.0)
        g.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.9
        gm.materials.clear()
        gm.materials.append(g)
    print("[bake] %d meshes baked in %.0fs" % (n, time.time() - t0))


def _baked_material(tag, col, nrm, rgh, metallic):
    m = bpy.data.materials.new("B_" + tag)
    m.use_nodes = True
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Metallic"].default_value = metallic
    b.inputs["Specular IOR Level"].default_value = 0.5
    tc = nt.nodes.new("ShaderNodeTexImage")
    tc.image = col
    nt.links.new(tc.outputs["Color"], b.inputs["Base Color"])
    tr = nt.nodes.new("ShaderNodeTexImage")
    tr.image = rgh
    nt.links.new(tr.outputs["Color"], b.inputs["Roughness"])
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = nrm
    nm = nt.nodes.new("ShaderNodeNormalMap")
    nm.space = "TANGENT"
    nt.links.new(tn.outputs["Color"], nm.inputs["Color"])
    nt.links.new(nm.outputs["Normal"], b.inputs["Normal"])
    return m


# --------------------------------------------------------------------------------------
# Lighting, cameras, render, export
# --------------------------------------------------------------------------------------
def setup_world_and_lights():
    scene = bpy.context.scene
    world = bpy.data.worlds.new("Sky")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    sky = nt.nodes.new("ShaderNodeTexSky")
    try:
        sky.sky_type = "MULTIPLE_SCATTERING"   # Blender 5.x name for the Nishita model
    except TypeError:
        sky.sky_type = "NISHITA"               # Blender 4.x
    sky.sun_elevation = math.radians(52)
    sky.sun_rotation = math.radians(210)
    sky.sun_intensity = 0.35
    sky.altitude = 600
    if hasattr(sky, "sun_disc"):
        sky.sun_disc = False                   # the Sun lamp below provides the direct light
    nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.45 if LOOK == "photo" else 0.35

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    sun.data.energy = 4.5 if LOOK == "photo" else 4.0
    sun.data.angle = math.radians(0.6 if LOOK == "photo" else 1.5)
    sun.data.color = (1.0, 0.96, 0.90)
    sun.rotation_euler = (math.radians(38), 0, math.radians(-30))
    coll("Lighting").objects.link(sun)


def setup_cameras():
    scene = bpy.context.scene
    top = bpy.data.objects.new("Cam_Top", bpy.data.cameras.new("Cam_Top"))
    top.data.type = "ORTHO"
    top.data.ortho_scale = 118.0
    top.data.clip_end = 500
    top.location = (0.0, 9.0, 200.0)
    top.rotation_euler = (0, 0, 0)
    coll("Cameras").objects.link(top)

    persp = bpy.data.objects.new("Cam_Persp", bpy.data.cameras.new("Cam_Persp"))
    persp.data.lens = 32
    persp.data.clip_end = 1000
    persp.location = (-52.0, -48.0, 34.0)
    target = Vector((2.0, 16.0, 1.0))
    persp.rotation_euler = (target - persp.location).to_track_quat("-Z", "Y").to_euler()
    coll("Cameras").objects.link(persp)

    eye = bpy.data.objects.new("Cam_Eye", bpy.data.cameras.new("Cam_Eye"))
    eye.data.lens = 24
    eye.data.clip_end = 1000
    eye.location = (-3.0, 2.2, 3.4)          # standing on the stage next to the DJ booth
    target = Vector((2.0, 30.0, 2.0))
    eye.rotation_euler = (target - eye.location).to_track_quat("-Z", "Y").to_euler()
    coll("Cameras").objects.link(eye)
    scene.camera = top
    return top, persp, eye


def render(camera, path, w, h, samples):
    scene = bpy.context.scene
    scene.camera = camera
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    has_oidn = getattr(bpy.app.build_options, "openimagedenoise", False)
    scene.cycles.samples = samples if has_oidn else samples * 2
    scene.cycles.use_denoising = bool(has_oidn)   # Ubuntu's apt Blender is built without OIDN
    scene.cycles.max_bounces = 8 if LOOK == "photo" else 4
    scene.cycles.transparent_max_bounces = 8
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False
    scene.view_settings.exposure = -0.7 if LOOK == "photo" else 0.0
    scene.render.resolution_x = w
    scene.render.resolution_y = h
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.compression = 50
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Punchy"
    scene.render.filepath = path
    t = time.time()
    bpy.ops.render.render(write_still=True)
    print("[render] %s  (%dx%d, %d samples, %.0fs)" % (path, w, h, samples, time.time() - t))


def triangle_count():
    total = 0
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        m = ob.data
        total += sum(len(p.vertices) - 2 for p in m.polygons)
    return total


def export_glb(path, draco=True, images="WEBP"):
    kwargs = dict(
        filepath=path, export_format="GLB", export_apply=True, export_extras=True,
        export_yup=True, export_lights=False, export_cameras=False, export_animations=False,
        export_skins=False, export_morph=False, export_image_format=images,
        export_image_quality=90, export_materials="EXPORT", export_normals=True,
        export_texcoords=True, export_draco_mesh_compression_enable=draco,
    )
    if draco:
        kwargs.update(export_draco_mesh_compression_level=6,
                      export_draco_position_quantization=14,
                      export_draco_normal_quantization=10,
                      export_draco_texcoord_quantization=12)
    bpy.ops.export_scene.gltf(**kwargs)
    size = os.path.getsize(path)
    print("[size] %s: %.1f KB (%.2f MB)" % (os.path.basename(path), size / 1024, size / 1048576))
    return size


# --------------------------------------------------------------------------------------
def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["blockout", "full"], default="full")
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--samples", type=int, default=48)
    ap.add_argument("--bake", action="store_true",
                    help="photo look: bake procedural materials to textures and export a GLB")
    ap.add_argument("--bake-res", type=int, default=1024, help="base bake resolution (px)")
    ap.add_argument("--cameras", default="top,perspective,eyelevel",
                    help="comma-separated subset of: top, perspective, eyelevel")
    ap.add_argument("--look", choices=["stylized", "photo"], default="stylized",
                    help="photo: procedural materials, backdrop and Cycles-only outputs")
    args = ap.parse_args(argv)
    global LOOK
    LOOK = args.look
    out = args.out
    os.makedirs(os.path.join(out, "renders"), exist_ok=True)

    t0 = time.time()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.length_unit = "METERS"
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"

    MATS["Atlas"], MATS["Water"] = make_materials(out)
    if LOOK == "photo":
        make_photo_materials()
    for c in ("Pools", "Cabanas_Lower", "Cabanas_Upper", "Bungalows", "Gaming", "Stage",
              "Backstage", "Bars", "Walkways"):
        coll(c)

    build_ground()
    build_pool()
    build_gaming_pavilion()
    build_structures()
    build_backstage()
    build_walkways()
    if args.stage == "blockout":
        build_blockout()
        tag = "stage1_blockout"
    else:
        build_cabanas()
        build_pool_furniture()
        build_deck_furniture()
        tag = "stage2_full"
        if LOOK == "photo":
            build_photo_details()
            build_context()
            tag = "photo"

    setup_world_and_lights()
    top, persp, eye = setup_cameras()

    tris = triangle_count()
    tables = [o for o in bpy.data.objects if o.name.startswith("T_")]
    print("[stats] objects=%d tables=%d unique_meshes=%d triangles=%d build=%.1fs"
          % (STATS["objects"], len(tables), len(bpy.data.meshes), tris, time.time() - t0))

    blend = os.path.join(out, "encore-beach-club%s.blend" % ("-photo" if LOOK == "photo" else ""))
    bpy.ops.wm.save_as_mainfile(filepath=blend, compress=True)
    print("[size] %s: %.1f KB" % (os.path.basename(blend), os.path.getsize(blend) / 1024))

    if LOOK == "photo":
        if args.bake:
            bake_all(os.path.join(out, "bake"), args.bake_res)
            export_glb(os.path.join(out, "encore-beach-club-photo.glb"), draco=True, images="AUTO")
        else:
            print("[export] photo look: pass --bake to bake materials and export encore-beach-club-photo.glb")
    elif args.stage == "full":
        export_glb(os.path.join(out, "renders", "encore-beach-club-uncompressed.glb"), draco=False)
        export_glb(os.path.join(out, "encore-beach-club.glb"), draco=True)
    else:
        export_glb(os.path.join(out, "renders", "encore-blockout.glb"), draco=True)

    if not args.no_render:
        for name in ("Gaming_Roof", "Backstage_Roof"):   # exported, but hidden in the previews
            if name in bpy.data.objects:
                bpy.data.objects[name].hide_render = True
        cams = [c.strip() for c in args.cameras.split(",")]
        if LOOK == "photo":
            if "perspective" in cams:
                render(persp, os.path.join(out, "renders", tag + "_perspective.png"), 1920, 1200, args.samples)
            if "eyelevel" in cams:
                render(eye, os.path.join(out, "renders", tag + "_eyelevel.png"), 1920, 1080, args.samples)
            if "top" in cams:
                render(top, os.path.join(out, "renders", tag + "_top.png"), 1600, 1700, args.samples)
        else:
            render(top, os.path.join(out, "renders", tag + "_top.png"), 1400, 1500, args.samples)
            if args.stage == "full":
                render(persp, os.path.join(out, "renders", tag + "_perspective.png"), 1600, 1000, args.samples)
    print("[done] total %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
