#!/usr/bin/env python3
"""STEP 뷰어 + 자동 정보 추출 (로컬 프로그램)

기본 사용법
  1) python step_viewer.py 실행 (또는 step_viewer_run.bat 더블클릭)
  2) [파일 선택 → 자동 모델링] 버튼을 누르고 STEP 파일을 고른다
  3) 메모장이 잠깐 열렸다 닫히며 내용을 읽고, 3D 창과 치수·부피·질량이 나온다
     (진행 중에는 키보드와 마우스를 건드리지 말 것. 3D 창을 닫으면 다음 파일을 고를 수 있다)

복잡한 형상도 창이 멈추지 않도록 계산은 별도 프로세스에서 하고, 한 번 연 모델은
  step_history 폴더에 결과(메시·치수)를 저장해 두어 다음부터 즉시 열린다.

STEP 파일 더블클릭으로 열기(한 번만 등록)
  step_viewer_파일연결_등록.bat 실행 (해제: step_viewer_파일연결_해제.bat)

그 밖의 사용법
  python step_viewer.py --clip                터미널에서 복사 후 Enter 방식
  python step_viewer.py 부품.stp              파일을 직접 열기
  python step_viewer.py --open                파일 선택 창으로 열기
  python step_viewer.py --info                창 없이 정보만 출력
  python step_viewer.py --png a.png           창 없이 이미지로 저장
  python step_viewer.py 폴더 --csv out.csv    폴더 안 모든 STEP을 표로 정리

설치(한 번만)
  pip install cadquery-ocp pyvista   (기존 cadquery 가 설치돼 있으면 그대로 사용 가능)

3D 창 조작: 왼쪽 드래그=회전, 휠=확대, Shift+드래그(또는 가운데 버튼)=이동
            Space/A: 자동 회전 켜기·끄기 / ↑↓: 회전 속도 / v: 정면 / t: 상면 / i: 등각
            x: 단면 보기(X→Y→Z→끔, 평면은 마우스로 이동) / d: 도면식 치수 표기 켜기·끄기(외형·Ø, 공차 없음)
            w: 와이어프레임 / s: 솔리드 / b: 바깥 사각 틀 켜기·끄기(기본 꺼짐) / q: 종료 (창 아래쪽에 단축키 안내가 표시됨)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

# exe 로 묶어 배포한 경우(PyInstaller)에는 exe 가 있는 폴더, 아니면 이 .py 가 있는 폴더
FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent


def self_command() -> list:
    """자기 자신을 다시 실행하는 명령 (exe 이면 exe 만, .py 이면 python + 스크립트)."""
    return [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]


STEP_EXTS = {".stp", ".step"}


def preload_occ():
    """CAD 엔진(OpenCascade) 모듈을 미리 불러 둔다. (cadquery 없이 직접 사용 → 시작이 빠르고 배포 용량이 작다)"""
    import OCP.BRepMesh  # noqa: F401
    import OCP.STEPControl  # noqa: F401


# ---------------------------------------------------------------- STEP 텍스트에서 읽는 부가 정보
def decode_step_string(s: str) -> str:
    """STEP의 \\X2\\C194...\\X0\\ (UTF-16 16진수) 한글 표기를 일반 문자열로 바꿉니다."""
    def repl(m):
        hexs = m.group(1)
        try:
            return "".join(chr(int(hexs[i:i + 4], 16)) for i in range(0, len(hexs), 4))
        except ValueError:
            return m.group(0)
    return re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", repl, s)


def read_step_meta(text: str) -> dict:
    """STEP 텍스트에 적힌 품명, 재질, 밀도, 작성 프로그램을 읽습니다. 없으면 빈 값."""
    meta = {"part": "", "material": "", "density": None, "software": "", "color": None}
    m = re.search(
        r"COLOUR_RGB\(\s*'[^']*'\s*,\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*,\s*([0-9.eE+-]+)\s*\)", text
    )
    if m:  # 파일에 적힌 표면 색상 (0~1 비율)
        r, g, b = (max(0, min(255, round(float(v) * 255))) for v in m.groups())
        meta["color"] = f"#{r:02x}{g:02x}{b:02x}"
    m = re.search(r"#\d+=PRODUCT\('([^']*)'\s*,\s*'([^']*)'", text)
    if m:
        meta["part"] = decode_step_string(m.group(1))
    m = re.search(r"DESCRIPTIVE_REPRESENTATION_ITEM\('([^']*)'", text)
    if m:
        meta["material"] = decode_step_string(m.group(1))
    m = re.search(r"'density measure'\s*,\s*POSITIVE_RATIO_MEASURE\(\s*([0-9.eE+-]+)\s*\)", text)
    if m:
        meta["density"] = float(m.group(1))  # 단위: g/cm^3 (Inventor 출력 기준)
    m = re.search(r"originating_system \*/\s*'([^']*)'", text)
    if m:
        meta["software"] = m.group(1)
    return meta


# ---------------------------------------------------------------- 형상 읽기와 분석
# STEP 을 읽을 때 OpenCascade 는 형상 보정(ShapeFix)을 하는데, 그중 '자기교차/와이어 교차 검사'가
# 변환 시간의 60~70%를 차지한다(프로파일 결과). CAD 프로그램이 정상 출력한 파일에는 거의 필요 없어서 끈다.
# 결과가 이상하면(부피 0 이하 등) 보정을 모두 켠 정밀 모드로 자동 재시도한다.
FAST_FIX = {"FixShape.FixSelfIntersectionMode": "0", "FixShape.FixIntersectingWiresMode": "0"}

SURF_NAMES = {  # GeomAbs_SurfaceType 순서
    0: "PLANE", 1: "CYLINDER", 2: "CONE", 3: "SPHERE", 4: "TORUS", 5: "BEZIER", 6: "BSPLINE",
    7: "REVOLUTION", 8: "EXTRUSION", 9: "OFFSET", 10: "OTHER",
}


CACHE_VER = 2  # 저장된 결과 형식 버전. 올리면 예전 결과는 자동으로 다시 계산된다(치수 표기 정보 추가)


def _check_step_text(text: str) -> str:
    text = text.lstrip("\ufeff \t\r\n")
    if not text.upper().startswith("ISO-10303-21"):
        raise ValueError(
            "STEP 형식이 아닙니다. 첫 줄이 'ISO-10303-21;' 인지 확인하세요. "
            "메모장에서 Ctrl+A로 전체 선택 후 복사했는지도 확인해 주세요. "
            f"(읽은 길이 {len(text)}자)"
        )
    if "END-ISO-10303-21" not in text.upper():
        raise ValueError("내용이 중간에서 끊겼습니다. 끝 줄 'END-ISO-10303-21;'까지 전체 복사해야 합니다.")
    return text


def load_shape_from_text(text: str, full_fix: bool = False):
    """STEP 텍스트를 임시 영문 파일로 내려 형상(TopoDS_Shape)을 읽습니다. 사본은 읽고 바로 지워집니다."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.Interface import Interface_Static
    from OCP.Resource import Resource_DataMapOfAsciiStringAsciiString
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TCollection import TCollection_AsciiString

    text = _check_step_text(text)
    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d) / "model.stp"
        tmp.write_text(text, encoding="utf-8", newline="\n")
        reader = STEPControl_Reader()
        if reader.ReadFile(str(tmp)) != IFSelect_RetDone:
            raise ValueError("STEP 파일을 해석하지 못했습니다.")
    if not full_fix:
        m = Resource_DataMapOfAsciiStringAsciiString()
        for k, v in FAST_FIX.items():
            m.Bind(TCollection_AsciiString(k), TCollection_AsciiString(v))
        reader.SetShapeFixParameters(m)
    reader.TransferRoots()
    shape = reader.OneShape()  # 여러 몸체가 있으면 전부 묶어서 가져옴
    if shape is None or shape.IsNull():
        raise ValueError("STEP에서 형상을 읽지 못했습니다.")
    return shape


def _faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        yield TopoDS.Face_s(e.Current())
        e.Next()


def exact_bbox(shape):
    """정확한 외형 치수. 빠른 근사 상자로 '바깥쪽에 닿는 면'만 골라 그 면들만 정밀 계산한다.
    (모든 면을 정밀 계산하면 복잡한 형상에서 1초 이상 걸림)"""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    faces = list(_faces(shape))
    if not faces:
        b = Bnd_Box()
        BRepBndLib.AddOptimal_s(shape, b, False, False)
        return b.Get()
    rough = []
    for f in faces:
        b = Bnd_Box()
        BRepBndLib.Add_s(f, b, False)
        rough.append(b.Get())
    exact = {}

    def refine(idx):
        for i in idx:
            b = Bnd_Box()
            BRepBndLib.AddOptimal_s(faces[i], b, False, False)
            exact[i] = b.Get()

    lo = [min(r[k] for r in rough) for k in range(3)]
    hi = [max(r[k + 3] for r in rough) for k in range(3)]
    # 1) 근사 경계에 닿는 면 중 해석적 곡면(평면·원통 등, 정밀 계산이 빠름)만 먼저 정밀 계산
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    touching = [i for i, r in enumerate(rough)
                if any(r[k] <= lo[k] + 1e-9 or r[k + 3] >= hi[k] - 1e-9 for k in range(3))]
    simple = []
    for i in touching:
        try:
            if int(BRepAdaptor_Surface(faces[i], False).GetType()) <= 4:  # PLANE..TORUS
                simple.append(i)
        except Exception:
            pass
    refine(simple or touching)
    while True:
        known = list(exact.values())
        lo = [min(b[k] for b in known) for k in range(3)]
        hi = [max(b[k + 3] for b in known) for k in range(3)]
        # 2) 정밀 계산 안 한 면 중 근사 상자가 현재 경계 밖으로 나가는 면이 있으면 그 면도 정밀 계산 후 반복
        out = [i for i, r in enumerate(rough) if i not in exact and
               any(r[k] < lo[k] - 1e-9 or r[k + 3] > hi[k] + 1e-9 for k in range(3))]
        if not out:
            break
        refine(out)
    return (*lo, *hi)


def _unique_edges(shape):
    """형상의 모서리를 중복 없이(면 2개가 공유하는 모서리도 한 번만) 돌려준다."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    try:
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape
        emap = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, emap)
        return [TopoDS.Edge_s(emap.FindKey(i)) for i in range(1, emap.Extent() + 1)]
    except ImportError:  # 라이브러리 버전에 따라 이름이 다를 때
        from OCP.TopExp import TopExp_Explorer
        seen, out = set(), []
        ex = TopExp_Explorer(shape, TopAbs_EDGE)
        while ex.More():
            e = TopoDS.Edge_s(ex.Current())
            h = hash(e)
            if h not in seen:
                seen.add(h)
                out.append(e)
            ex.Next()
        return out


def _volume(shape) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def analyze_text(text: str, name: str, default_density: float | None = None, full_fix: bool = False) -> dict:
    import time as _t
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Circle

    t0 = _t.perf_counter()
    shape = load_shape_from_text(text, full_fix)
    t1 = _t.perf_counter()
    volume = _volume(shape)  # mm^3
    if not full_fix and not volume > 1e-9:
        # 빠른 모드 결과가 이상하면(부피 0 이하) 형상 보정을 모두 켜고 다시 읽는다
        shape = load_shape_from_text(text, True)
        volume = _volume(shape)
        full_fix = True
        t1 = _t.perf_counter()
    t2 = _t.perf_counter()
    x0, y0, z0, x1, y1, z1 = exact_bbox(shape)
    t3 = _t.perf_counter()
    meta = read_step_meta(text)
    density = meta["density"] or default_density
    mass = volume / 1000.0 * density if density else None

    face_types, nfaces = Counter(), 0
    for f in _faces(shape):
        nfaces += 1
        try:
            face_types[SURF_NAMES.get(int(BRepAdaptor_Surface(f, False).GetType()), "OTHER")] += 1
        except Exception:
            face_types["OTHER"] += 1

    # 원형 모서리의 지름 목록: 구멍, 외경, 필렛 경계 등을 한눈에 보기 위함 (같은 모서리는 한 번만)
    diams = Counter()
    circles = []  # 도면식 Ø 표기용: [지름, 중심 xyz, 축 방향 xyz, 호의 각도]
    for e in _unique_edges(shape):
        try:
            if BRep_Tool.Degenerated_s(e):
                continue
            c = BRepAdaptor_Curve(e)
            if c.GetType() == GeomAbs_Circle:
                circ = c.Circle()
                diams[round(circ.Radius() * 2, 3)] += 1
                loc, ax = circ.Location(), circ.Axis().Direction()
                circles.append([round(circ.Radius() * 2, 4), loc.X(), loc.Y(), loc.Z(), ax.X(), ax.Y(), ax.Z(),
                                abs(c.LastParameter() - c.FirstParameter())])
        except Exception:
            pass
    t4 = _t.perf_counter()
    detail = {"import": round(t1 - t0, 1), "bbox": round(t3 - t2, 1),
              "volume": round(t2 - t1, 1), "rest": round(t4 - t3, 1)}
    dx, dy, dz = x1 - x0, y1 - y0, z1 - z0

    return {
        "file": name,
        "part": meta["part"],
        "material": meta["material"],
        "density_g_cm3": density,
        "file_density": meta["density"],
        "timing_detail": detail,
        "fix_mode": "full" if full_fix else "fast",
        "software": meta["software"],
        "color": meta["color"],
        "size_x": round(dx, 3),
        "size_y": round(dy, 3),
        "size_z": round(dz, 3),
        "volume_mm3": round(volume, 2),
        "mass_g": round(mass, 3) if mass is not None else None,
        "faces": nfaces,
        "face_types": dict(face_types),
        "circle_diameters": dict(sorted(diams.items())),
        "_shape": shape,
        "bbox": [x0, y0, z0, x1, y1, z1],
        "circles_geo": circles,
        "cache_ver": CACHE_VER,
        "_meshp": mesh_params((dx * dx + dy * dy + dz * dz) ** 0.5, nfaces),
    }


def analyze_file(path: Path, default_density: float | None = None, full_fix: bool = False) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as ex:
        raise ValueError(f"파일을 읽을 수 없습니다: {ex}") from ex
    text = raw.decode("utf-8", errors="replace")
    return analyze_text(text, path.name, default_density, full_fix)


def format_info(info: dict) -> str:
    lines = [
        f"출처      : {info['file']}",
        f"품명      : {info['part'] or '-'}",
        f"재질      : {info['material'] or '-'}"
        + (f" (밀도 {info['density_g_cm3']:g} g/cm³)" if info["density_g_cm3"] else ""),
        f"작성      : {info['software'] or '-'}",
        f"외형 치수 : X {info['size_x']} × Y {info['size_y']} × Z {info['size_z']} mm",
        f"부피      : {info['volume_mm3']} mm³",
        "질량      : " + (f"{info['mass_g']} g" if info["mass_g"] is not None else "밀도 정보 없음"),
        f"면 개수   : {info['faces']}  " + ", ".join(f"{k} {v}" for k, v in info["face_types"].items()),
    ]
    if info["circle_diameters"]:
        lines.append(
            "원형 모서리 지름(mm): "
            + ", ".join(f"Ø{d:g}×{n}" for d, n in info["circle_diameters"].items())
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- 3D 표시
def format_overlay(info: dict) -> str:
    """3D 창 안에 쓰는 글자. 기본 글꼴에 한글이 없어서 영문 라벨과 ASCII만 씁니다."""
    ascii_part = info["part"] if info["part"].isascii() else info["file"]
    lines = [
        info["file"] if info["file"].isascii() else ascii_part,
        f"Part: {ascii_part}",
        f"Size: {info['size_x']} x {info['size_y']} x {info['size_z']} mm",
        f"Volume: {info['volume_mm3']} mm3",
    ]
    if info["mass_g"] is not None:
        mat = info["material"] if info["material"].isascii() else ""
        lines.append(f"Mass: {info['mass_g']} g  {mat} ({info['density_g_cm3']:g} g/cm3)".rstrip())
    return "\n".join(lines)


def mesh_params(diag: float, nfaces: int):
    """형상 크기와 면 개수에 따라 메시 정밀도를 정한다. (공차 mm, 각도 rad)"""
    tol = max(0.02, diag * 0.0004)  # 작은 부품은 0.02mm, 큰 부품은 크기에 비례해서 완화
    # 면이 많을수록(형상이 복잡할수록) 단계적으로 완화.
    if nfaces <= 100:
        ang = 0.15
    elif nfaces <= 300:
        ang = 0.3
    elif nfaces <= 1000:
        ang = 0.45
    elif nfaces <= 3000:
        ang = 0.6
        tol = max(tol, diag * 0.001)
    else:
        ang = 0.8
        tol = max(tol, diag * 0.002)
    return tol, ang


def _mesh_shape(shape, tol: float, ang: float):
    """1차: Delabella 알고리즘(기본 알고리즘보다 약 4배 빠름)으로 전체를 메시.
    2차: Delabella 가 못 만든 면만 기본 알고리즘으로 채움(이미 된 면은 건너뜀). 모두 CPU 코어 병렬."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    try:
        from OCP.BRepMesh import (BRepMesh_DelabellaMeshAlgoFactory, BRepMesh_EdgeDiscret, BRepMesh_FaceDiscret,
                                  BRepMesh_ModelBuilder, BRepMesh_ModelHealer, BRepMesh_ModelPostProcessor,
                                  BRepMesh_ModelPreProcessor)
        from OCP.IMeshTools import IMeshTools_Context
        ctx = IMeshTools_Context()
        ctx.SetModelBuilder(BRepMesh_ModelBuilder())
        ctx.SetEdgeDiscret(BRepMesh_EdgeDiscret())
        ctx.SetModelHealer(BRepMesh_ModelHealer())
        ctx.SetPreProcessor(BRepMesh_ModelPreProcessor())
        ctx.SetFaceDiscret(BRepMesh_FaceDiscret(BRepMesh_DelabellaMeshAlgoFactory()))
        ctx.SetPostProcessor(BRepMesh_ModelPostProcessor())
        mesher = BRepMesh_IncrementalMesh()
        mesher.SetShape(shape)
        prm = mesher.ChangeParameters()
        prm.Deflection, prm.Angle, prm.InParallel = tol, ang, True
        mesher.Perform(ctx)
    except Exception:
        pass
    BRepMesh_IncrementalMesh(shape, tol, False, ang, True)  # 빈 면 채우기 (또는 1차 실패 시 전체)


def build_mesh(shape, params=None):
    """OpenCascade 메시 → 바이너리 STL → pyvista. (점마다 파이썬 객체를 만들지 않아 빠름)"""
    import pyvista as pv

    if params is None:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b, False)
        x0, y0, z0, x1, y1, z1 = b.Get()
        params = mesh_params(((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) ** 0.5, sum(1 for _ in _faces(shape)))
    tol, ang = params
    _mesh_shape(shape, tol, ang)
    from OCP.StlAPI import StlAPI_Writer
    w = StlAPI_Writer()
    w.ASCIIMode = False
    fd, path = tempfile.mkstemp(suffix=".stl")
    os.close(fd)
    try:
        w.Write(shape, path)
        mesh = pv.read(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if mesh.n_cells == 0:
        raise RuntimeError("3D 메시를 만들지 못했습니다.")
    return mesh.clean()  # STL 은 삼각형마다 점이 따로라서 같은 위치 점을 합쳐야 모서리선이 제대로 나온다


DEFAULT_COLOR = "#b0b7bd"  # 파일에 색이 없을 때 쓰는 은회색

SHORTCUT_MEMO = (
    "Mouse: L-drag = rotate,  Wheel = zoom,  Shift+L-drag or M-drag = pan\n"
    "Keys : Space or A = auto-rotate,  Up/Down = rotate speed,  Q = quit\n"
    "View : V = front,  T = top,  I = iso,  W = wireframe,  S = solid,  B = box on/off\n"
    "Tools: X = section (X/Y/Z/off, drag plane),  D = dimensions on/off"
)


def _fmt_mm(v: float) -> str:
    """도면식 숫자 표기: 16.000 → 16, 8.900 → 8.9"""
    t = f"{v:.3f}".rstrip("0").rstrip(".")
    return t if t not in ("", "-0") else "0"


# 구멍·축 지름 표기 색 (모델 색과 구분되는 진한 색들). 라벨 바탕·고리·지시선이 같은 색
HOLE_COLORS = ["#1f6feb", "#2a9d3b", "#8e44ad", "#d6336c", "#0f9fb0", "#b8860b", "#5b6ee1", "#c2410c",
               "#15803d", "#7c3aed", "#0369a1", "#be123c"]
_DIM_META = {}  # 3D 화면별 치수 표기 상태(겹쳐 그리는 층, 구멍별 요소)


def _ring(c, n, r, seg=72):
    """중심 c, 축 n, 반지름 r 인 원(닫힌 선)."""
    import numpy as np
    import pyvista as pv
    c, n = np.asarray(c, float), np.asarray(n, float)
    a = np.cross(n, [0, 0, 1]) if abs(n[2]) < 0.9 else np.cross(n, [1, 0, 0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    t = np.linspace(0, 2 * np.pi, seg + 1)
    pts = c + r * (np.outer(np.cos(t), a) + np.outer(np.sin(t), b))
    return pv.lines_from_points(pts)


def _dim_overlay(pl):
    """모델 위에 겹쳐 그리는 층(가려진 부분도 보이게). 3D 화면마다 하나 만들어 재사용한다."""
    from vtkmodules.vtkRenderingCore import vtkRenderer
    meta = _DIM_META.get(id(pl))
    ov = meta["overlay"] if meta else None
    if ov is None:
        ov = vtkRenderer()
        ov.SetLayer(1)
        ov.InteractiveOff()
        rw = pl.ren_win
        rw.SetNumberOfLayers(max(2, rw.GetNumberOfLayers()))
        rw.AddRenderer(ov)
    ov.SetViewport(pl.renderer.GetViewport())
    ov.SetActiveCamera(pl.renderer.GetActiveCamera())
    ov.RemoveAllViewProps()
    ov.SetDraw(True)
    return ov


def _overlay_add(ov, poly, color, width=1.5, opacity=1.0):
    import pyvista as pv
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper
    mp = vtkPolyDataMapper()
    mp.SetInputData(poly)
    act = vtkActor()
    act.SetMapper(mp)
    prop = act.GetProperty()
    prop.SetColor(pv.Color(color).float_rgb)
    prop.SetLineWidth(width)
    prop.SetOpacity(opacity)
    prop.LightingOff()
    ov.AddActor(act)
    return act


def set_dims_visible(pl, names, on: bool):
    """치수 표기 전체 켜고 끄기 (보통 층 + 겹쳐 그리는 층)."""
    for nm, act in list(pl.renderer.actors.items()):
        if any(str(nm) == d or str(nm).startswith(d + "-") for d in names):
            act.SetVisibility(on)
    meta = _DIM_META.get(id(pl))
    if meta and meta.get("overlay") is not None:
        meta["overlay"].SetDraw(on)


def highlight_hole(pl, idx):
    """패널에서 고른 지름 하나만 강조(굵게·진하게), 나머지는 흐리게. idx=None 이면 원래대로."""
    meta = _DIM_META.get(id(pl))
    if not meta:
        return
    for i, h in enumerate(meta["holes"]):
        sel = idx is None or i == idx
        h["main"].GetProperty().SetLineWidth(6 if (idx is not None and i == idx) else 3)
        h["main"].GetProperty().SetOpacity(1.0 if sel else 0.15)
        h["ov_ring"].GetProperty().SetOpacity(0.8 if (idx is not None and i == idx) else (0.35 if sel else 0.05))
        h["ov_lead"].GetProperty().SetOpacity(0.9 if sel else 0.08)
        lab = pl.renderer.actors.get(h["label"] + "-labels")
        if lab is not None:
            lab.SetVisibility(sel)
    pl.render()


def hole_groups(info: dict, max_labels: int = 12) -> list:
    """원형 모서리에서 '구멍·축 지름' 표기 목록을 만든다. → [(지름, 개수, 중심, 축방향), ...] 큰 지름부터.
    - 반원 2개로 나뉜 모서리는 합쳐서 완전한 원만 인정(필렛 등 부분 호는 제외)
    - 같은 축선·같은 지름(구멍의 위·아래 모서리)은 하나로 셈
    - 같은 지름이 여러 곳이면 '개수×Ø' 로 묶음 (도면 표기와 같게)"""
    import math
    full = {}
    for d, cx, cy, cz, nx, ny, nz, span in info.get("circles_geo") or []:
        n = (nx, ny, nz)
        for comp in n:  # 축 방향 부호 통일
            if abs(comp) > 1e-9:
                if comp < 0:
                    n = (-nx, -ny, -nz)
                break
        t = cx * n[0] + cy * n[1] + cz * n[2]
        foot = (cx - t * n[0], cy - t * n[1], cz - t * n[2])  # 축선의 위치
        key = (round(d, 3), tuple(round(v, 3) for v in n), tuple(round(v, 2) for v in foot), round(t, 3))
        g = full.setdefault(key, {"span": 0.0, "c": (cx, cy, cz), "n": n})
        g["span"] += span
    by_axis = {}   # 축선별: 그 축에 있는 같은 지름의 완전한 원 전부(구멍 양 끝 모서리 등)
    for k, g in full.items():
        if g["span"] >= 2 * math.pi - 1e-3:
            by_axis.setdefault(k[:3], []).append(g)
    by_d = {}
    for k, gs in by_axis.items():
        by_d.setdefault(k[0], []).append(gs)
    out = []
    for d, axes in sorted(by_d.items(), key=lambda kv: -kv[0])[:max_labels]:
        rings = [(g["c"], g["n"]) for gs in axes for g in gs]
        # 지시선은 기본 등각 시점(오른쪽 위 앞)에서 가장 앞쪽에 있는 원에서 뺀다
        front = max(rings, key=lambda r: r[0][0] + r[0][1] + r[0][2])
        out.append((d, len(axes), front[0], front[1], rings))
    return out


def add_dimensions(pl, info: dict) -> list:
    """도면처럼 전체 외형(가로·세로·높이) 치수선과 구멍·축 지름(Ø)을 3D 화면에 표기한다.
    공차는 STEP(AP214)에 들어 있지 않아 표기하지 않는다. 추가한 요소 이름 목록을 돌려준다."""
    import numpy as np
    import pyvista as pv

    bb = info.get("bbox")
    if not bb:
        return []
    x0, y0, z0, x1, y1, z1 = bb
    L = max(x1 - x0, y1 - y0, z1 - z0) or 1.0
    m = 0.12 * L          # 모델에서 치수선까지 띄우는 거리
    ah = 0.035 * L        # 화살표 길이
    col, tcol = "#1f4e79", "#0b2a4a"
    lines, arrows, lab_pts, lab_txt = [], [], [], []

    def dim(p, q, ext_from_p, ext_from_q, text):
        p, q = np.array(p, float), np.array(q, float)
        lines.append(pv.Line(p, q))
        for a, b in ((ext_from_p, p), (ext_from_q, q)):   # 보조선(모델 → 치수선)
            a = np.array(a, float)
            lines.append(pv.Line(a, b + (b - a) / (np.linalg.norm(b - a) or 1) * 0.25 * m))
        u = (q - p) / (np.linalg.norm(q - p) or 1)
        arrows.append(pv.Cone(center=p + u * ah / 2, direction=-u, height=ah, radius=ah * 0.3, resolution=12))
        arrows.append(pv.Cone(center=q - u * ah / 2, direction=u, height=ah, radius=ah * 0.3, resolution=12))
        lab_pts.append((p + q) / 2)
        lab_txt.append(text)

    # 기본 등각 시점(오른쪽 위 앞에서 내려다봄) 기준으로 보이는 바깥 모서리에 배치
    # 가로(X): 앞쪽 바닥 모서리, 세로(Y): 오른쪽 바닥 모서리, 높이(Z): 왼쪽 앞 세로 모서리
    dim((x0, y1 + m, z0), (x1, y1 + m, z0), (x0, y1, z0), (x1, y1, z0), _fmt_mm(x1 - x0))
    dim((x1 + m, y0, z0), (x1 + m, y1, z0), (x1, y0, z0), (x1, y1, z0), _fmt_mm(y1 - y0))
    dim((x0 - m * 0.7, y1 + m * 0.7, z0), (x0 - m * 0.7, y1 + m * 0.7, z1), (x0, y1, z0), (x0, y1, z1),
        _fmt_mm(z1 - z0))

    names = []

    def label(pt, text, color, nm):
        # 라벨을 하나씩 따로 추가: 한꺼번에 넣으면 겹치는 라벨을 VTK 가 자동으로 숨겨 버린다
        pl.add_point_labels(np.array([pt]), [text], font_size=14, text_color=color, bold=True,
                            shape="rounded_rect", shape_color="white", shape_opacity=0.85, point_size=1,
                            show_points=False, always_visible=True, name=nm)
        names.append(nm)

    pl.add_mesh(pv.merge(lines), color=col, line_width=2, name="dim_lines", pickable=False)
    pl.add_mesh(pv.merge(arrows), color=col, name="dim_arrows", pickable=False)
    names += ["dim_lines", "dim_arrows"]
    for i, (pt, t) in enumerate(zip(lab_pts, lab_txt)):
        label(pt, t, tcol, f"dim_label{i}")

    # 구멍·축 지름: 가리키는 원 자체를 색깔 고리로 칠하고, 같은 색 지시선·같은 색 라벨로 연결한다.
    # - 고리는 보이는 부분은 진하게, 모델에 가려진 부분은 연하게(겹쳐 그리는 층) → 어느 원인지 바로 보임
    # - 같은 지름이 여러 곳이면 모든 원에 같은 색 고리 → '4×Ø5' 가 어디 어디인지 보임
    ov = _dim_overlay(pl)
    meta = {"overlay": ov, "holes": []}
    _DIM_META[id(pl)] = meta
    holes = hole_groups(info)
    if holes:
        ctr = np.array([(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2])
        axis_seen = {}
        for i, (d, cnt, c, n, rings) in enumerate(holes):
            colr = HOLE_COLORS[i % len(HOLE_COLORS)]
            c, n = np.array(c, float), np.array(n, float)
            ring_poly = pv.merge([_ring(rc, rn, d / 2) for rc, rn in rings])
            main_ring = pl.add_mesh(ring_poly, color=colr, line_width=3, name=f"hole_ring{i}", pickable=False)
            names.append(f"hole_ring{i}")
            w = np.array([1.0, 1.0, 1.0]) + (c - ctr) / L   # 모델 바깥(오른쪽 위) 방향으로 지시선을 뺀다
            u = w - w.dot(n) * n
            if np.linalg.norm(u) < 1e-6:
                u = np.cross(n, [0, 0, 1]) if abs(n[2]) < 0.9 else np.cross(n, [1, 0, 0])
            u /= np.linalg.norm(u)
            # 같은 축에 여러 지름(단차 보어 등)이 있으면 지시선 방향을 축 둘레로 45°씩 돌려 라벨이 겹치지 않게
            akey = tuple(np.round(c - c.dot(n) * n, 2)) + tuple(np.round(n, 3))
            k = axis_seen.get(akey, 0)
            axis_seen[akey] = k + 1
            if k:
                ang = np.radians(45.0 * k)
                u = u * np.cos(ang) + np.cross(n, u) * np.sin(ang)
            on = c + u * d / 2                      # 원 둘레 위의 점
            tip = on + (u + n * 0.4) / np.linalg.norm(u + n * 0.4) * (0.16 + 0.04 * (k % 3)) * L
            leader = pv.merge([pv.Line(on, tip), pv.Sphere(radius=0.008 * L, center=on)])
            # 겹쳐 그리는 층: 가려진 고리(연하게)와 지시선(모델을 뚫고 라벨까지 이어지게)
            ov_ring = _overlay_add(ov, ring_poly, colr, width=1.5, opacity=0.35)
            ov_lead = _overlay_add(ov, leader, colr, width=1.5, opacity=0.9)
            txt = (f"{cnt}×" if cnt > 1 else "") + "Ø" + _fmt_mm(d)
            nm = f"hole_label{i}"
            pl.add_point_labels(np.array([tip]), [txt], font_size=13, text_color="white", bold=True,
                                shape="rounded_rect", shape_color=colr, shape_opacity=0.92, point_size=1,
                                show_points=False, always_visible=True, name=nm)
            names.append(nm)
            meta["holes"].append({"main": main_ring, "ov_ring": ov_ring, "ov_lead": ov_lead, "label": nm})
    return names


def show(info: dict, png: str | None = None, color: str | None = None):
    """색상 우선순위: 직접 지정한 색 > STEP 파일에 적힌 색 > 은회색"""
    import pyvista as pv

    mesh = info["_mesh"] if info.get("_mesh") is not None else build_mesh(info["_shape"], info.get("_meshp"))
    pl = pv.Plotter(off_screen=bool(png), window_size=(1100, 780), title="STEP viewer")
    model_color = color or info.get("color") or DEFAULT_COLOR
    body = pl.add_mesh(mesh, color=model_color, smooth_shading=False, specular=0.4, split_sharp_edges=True)
    # 면마다 따로 쪼개져 있으므로 면의 경계선이 곧 실제 모서리입니다.
    edges = mesh.extract_feature_edges(feature_angle=35, boundary_edges=True, non_manifold_edges=False)
    edge_actor = pl.add_mesh(edges, color="#222222", line_width=1) if edges.n_points else None
    pl.add_axes()
    pl.set_background("#eef0ee")
    pl.add_text(format_overlay(info), position="upper_left", font_size=9, color="#1d2421")
    # 바깥 사각 틀: B 키로 켜고 끔(기본 꺼짐). pyvista 내장 상자는 치수선·단면 등 요소를 추가할 때마다
    # 다시 만들어지며 저절로 켜지는 문제가 있어(B 키와 충돌), 모델 외곽선을 직접 그린다.
    bbox = pl.add_mesh(pv.Box(bounds=mesh.bounds).outline(), color="#999999", line_width=1,
                       name="box_outline", pickable=False)
    bbox.SetVisibility(False)
    pl.camera_position = "iso"
    pl.renderer.reset_camera_clipping_range()

    # 도면식 치수 표기 (기본 켜짐, D 키로 켜고 끔)
    try:
        dim_names = add_dimensions(pl, info)
    except Exception:
        dim_names = []
    pl.camera_position = "iso"   # 치수선까지 화면에 다 들어오도록 시점을 다시 맞춤
    pl.renderer.reset_camera_clipping_range()

    # 창 아래쪽 단축키 안내 (VTK 기본 글꼴에 한글이 없어 영문으로 표시)
    pl.add_text(SHORTCUT_MEMO, position="lower_left", font_size=8, color="#1d2421", name="memo")

    if png:
        pl.show(screenshot=png, auto_close=True)
        return

    # pyvista 기본 단축키(V=등각, B=선택, ↑↓=확대 등)가 우리 단축키와 겹쳐 두 동작이 동시에 일어나므로
    # 기본 단축키를 지우고 필요한 것만 다시 등록한다. (W/S 등 VTK 자체 키는 그대로 동작)
    try:
        pl.iren.clear_key_event_callbacks()
        if hasattr(pl, "_prep_for_close"):
            pl.add_key_event("q", pl._prep_for_close)
    except Exception:
        pass

    # ---- 자동 회전: Space(또는 A)로 켜고 끔, 위/아래 화살표로 속도 조절
    # (+/- 는 VTK 기본 기능(선 굵기)과 겹쳐서 쓰지 않는다)
    state = {"on": False, "speed": 1.0}   # speed: 프레임당 회전 각도(도)
    tools = {"section": None, "clip_names": [], "dims": bool(dim_names), "box": False}

    def draw_status():
        parts = [f"AUTO-ROTATE: ON ({state['speed']:.1f})" if state["on"] else "AUTO-ROTATE: OFF (Space)"]
        if tools["section"]:
            parts.append(f"SECTION: {tools['section'].upper()}")
        parts.append("DIM: ON (D)" if tools["dims"] else "DIM: OFF (D)")
        pl.add_text("   ".join(parts), position="lower_right", font_size=9, color="#1d2421", name="rot")
        apply_box()

    # ---- 단면 보기: X 키를 누를 때마다 X → Y → Z 평면 → 끔. 평면은 마우스로 끌어 옮기거나 화살표로 기울일 수 있음
    def section_off():
        try:
            pl.clear_plane_widgets()
        except Exception:
            pass
        # 단면 모델과 함께 생긴 보조 요소(평면 외곽선 등)까지 모두 지운다
        for nm in tools["clip_names"]:
            try:
                pl.remove_actor(nm)
            except Exception:
                pass
        tools["clip_names"] = []
        body.SetVisibility(True)
        if edge_actor is not None:
            edge_actor.SetVisibility(True)
        tools["section"] = None

    def cycle_section():
        nxt = {None: "x", "x": "y", "y": "z", "z": None}[tools["section"]]
        section_off()
        if nxt:
            body.SetVisibility(False)
            if edge_actor is not None:
                edge_actor.SetVisibility(False)
            # 잘린 안쪽 면은 붉은색으로 보여 단면 위치를 알아보기 쉽게 한다
            before = set(pl.renderer.actors.keys())
            pl.add_mesh_clip_plane(
                mesh, normal=nxt, crinkle=False, color=model_color, smooth_shading=False,
                backface_params={"color": "#c0392b"}, widget_color="#1f6feb", outline_opacity=0.15)
            tools["clip_names"] = [k for k in pl.renderer.actors.keys() if k not in before]
            tools["section"] = nxt
        draw_status()
        pl.render()

    # ---- 치수 표기: D 키로 켜고 끔 (단면 보기 중에도 그대로 보임)
    def toggle_dims():
        tools["dims"] = not tools["dims"]
        set_dims_visible(pl, dim_names, tools["dims"])
        draw_status()
        pl.render()

    def toggle_rotate():
        state["on"] = not state["on"]
        draw_status()
        pl.render()

    def change_speed(delta):
        state["speed"] = min(8.0, max(0.2, state["speed"] + delta))
        draw_status()
        pl.render()

    def apply_box():
        bbox.SetVisibility(tools["box"])

    def toggle_box():
        tools["box"] = not tools["box"]
        apply_box()
        pl.render()

    draw_status()
    pl.add_key_event("b", toggle_box)
    pl.add_key_event("space", toggle_rotate)
    pl.add_key_event("a", toggle_rotate)
    pl.add_key_event("Up", lambda: change_speed(+0.5))
    pl.add_key_event("Down", lambda: change_speed(-0.5))
    pl.add_key_event("v", lambda: (pl.view_xy(), pl.render()))
    pl.add_key_event("t", lambda: (pl.view_xz(), pl.render()))
    pl.add_key_event("i", lambda: (pl.view_isometric(), pl.render()))
    pl.add_key_event("x", cycle_section)
    pl.add_key_event("d", toggle_dims)

    # 타이머 대신 직접 갱신 루프. 창을 닫은 뒤 그리기를 시도하면 프로그램이 죽으므로
    # (access violation) 닫힘 신호를 받는 즉시 루프를 빠져나온다.
    import time
    closed = {"v": False}

    def mark_closed(*_a):
        closed["v"] = True

    pl.show(interactive_update=True, auto_close=False)
    for ev in ("ExitEvent", "DeleteEvent"):
        try:
            pl.iren.add_observer(ev, mark_closed)
        except Exception:
            pass

    def window_alive():
        if closed["v"] or getattr(pl, "_closed", False):
            return False
        try:
            return bool(pl.ren_win.GetMapped())
        except Exception:
            return False

    try:
        while window_alive():
            pl.iren.process_events()          # 마우스·키 입력 처리 (여기서 창이 닫힐 수 있음)
            if not window_alive():
                break
            if state["on"]:
                pl.camera.Azimuth(state["speed"])
            # 회전·확대·이동으로 모델과 카메라 거리가 바뀌면 앞뒤 절단면(clipping)이 맞지 않아
            # 모델이 잘려 보이므로, 매 프레임 모델 크기에 맞게 다시 계산한다.
            pl.renderer.reset_camera_clipping_range()
            pl.render()
            time.sleep(0.015)
    except Exception:
        pass
    finally:
        # 이미 닫힌 창은 건드리지 않는다. 아직 남아 있을 때만 정리.
        if not closed["v"] and not getattr(pl, "_closed", False):
            try:
                pl.close()
            except Exception:
                pass


# ---------------------------------------------------------------- 입력
def get_clipboard_text() -> str:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tk.TclError:
        return ""
    finally:
        root.destroy()


def pick_file() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    p = filedialog.askopenfilename(title="STEP 파일 선택", filetypes=[("STEP", "*.stp *.step"), ("모든 파일", "*.*")])
    root.destroy()
    return Path(p) if p else None


def batch(folder: Path, out_csv: Path, density: float | None):
    rows = []
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in STEP_EXTS)
    for p in files:
        try:
            info = analyze_file(p, density)
            info.pop("_shape")
            for k in ("file_density", "_meshp", "timing_detail", "circles_geo", "bbox", "cache_ver"):
                info.pop(k, None)
            info["face_types"] = "; ".join(f"{k}:{v}" for k, v in info["face_types"].items())
            info["circle_diameters"] = "; ".join(f"{d:g}x{n}" for d, n in info["circle_diameters"].items())
            rows.append(info)
            print(f"OK  {p.name}")
        except Exception as ex:  # 한 파일이 실패해도 나머지는 계속
            print(f"실패 {p.name}: {ex}")
            rows.append({"file": p.name, "part": f"읽기 실패: {ex}"})
    if not rows:
        print("STEP 파일을 찾지 못했습니다.")
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀에서 한글 정상 표시
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows)}개 파일 → {out_csv}")


class _WinNotepad:
    """윈도우 전용 부분: 메모장 실행, 창 찾기, 입력칸 글자 읽기, (예비) Ctrl+A/C, 닫기."""

    EDIT_CLASSES = ("Edit", "RichEditD2DPT", "RICHEDIT50W", "RichEdit20W", "RichEdit20A")

    def __init__(self):
        import ctypes
        self.ct = ctypes
        self.u = ctypes.windll.user32
        c, u = ctypes, self.u
        self.PROC = c.WINFUNCTYPE(c.c_bool, c.c_void_p, c.c_void_p)
        u.EnumWindows.argtypes = [self.PROC, c.c_void_p]
        u.EnumChildWindows.argtypes = [c.c_void_p, self.PROC, c.c_void_p]
        u.IsWindowVisible.argtypes = [c.c_void_p]
        u.GetWindowTextLengthW.argtypes = [c.c_void_p]
        u.GetWindowTextW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_int]
        u.GetClassNameW.argtypes = [c.c_void_p, c.c_wchar_p, c.c_int]
        u.ShowWindow.argtypes = [c.c_void_p, c.c_int]
        u.SetForegroundWindow.argtypes = [c.c_void_p]
        u.GetForegroundWindow.restype = c.c_void_p
        u.PostMessageW.argtypes = [c.c_void_p, c.c_uint, c.c_size_t, c.c_ssize_t]
        u.SendMessageW.argtypes = [c.c_void_p, c.c_uint, c.c_size_t, c.c_void_p]
        u.SendMessageW.restype = c.c_ssize_t
        self.proc = None

    def _cls(self, h):
        b = self.ct.create_unicode_buffer(256)
        self.u.GetClassNameW(h, b, 256)
        return b.value

    def _title(self, h):
        n = self.u.GetWindowTextLengthW(h)
        if not n:
            return ""
        b = self.ct.create_unicode_buffer(n + 1)
        self.u.GetWindowTextW(h, b, n + 1)
        return b.value

    def _enum(self, parent=None):
        out = []

        def cb(h, _):
            out.append(h)
            return True

        proc = self.PROC(cb)  # 호출이 끝날 때까지 참조를 유지
        if parent is None:
            self.u.EnumWindows(proc, 0)
        else:
            self.u.EnumChildWindows(parent, proc, 0)
        return out

    def _notepad_windows(self):
        res = []
        for h in self._enum():
            if not self.u.IsWindowVisible(h):
                continue
            c, t = self._cls(h), self._title(h)
            if c == "Notepad" or t.endswith("메모장") or t.endswith("Notepad"):
                res.append((h, c, t))
        return res

    def snapshot(self):
        return {h for h, _, _ in self._notepad_windows()}

    def launch(self, path):
        import subprocess
        self.proc = subprocess.Popen(["notepad.exe", str(path)])

    def find_window(self, base, before):
        """실행 전에 없던 메모장 창 > 제목에 파일명이 든 창 > 맨 앞의 메모장 창 순서로 찾는다."""
        import unicodedata
        wins = self._notepad_windows()
        new = [h for h, _, _ in wins if h not in before]
        if new:
            return new[0]
        nb = unicodedata.normalize("NFC", base).lower()  # 한글 자모 분리 표기 차이를 흡수
        for h, _, t in wins:
            if nb in unicodedata.normalize("NFC", t).lower():
                return h
        fg = self.u.GetForegroundWindow()
        for h, _, _ in wins:
            if h == fg:
                return h
        return None

    def read_text(self, hwnd):
        """메모장 입력칸의 글자를 직접 읽는다(키보드·클립보드를 쓰지 않음)."""
        ct, u = self.ct, self.u
        best = ""
        for ch in self._enum(hwnd):
            if self._cls(ch) not in self.EDIT_CLASSES:
                continue
            n = u.SendMessageW(ch, 0x000E, 0, None)  # WM_GETTEXTLENGTH
            if n and n > 0:
                buf = ct.create_unicode_buffer(n + 1)
                u.SendMessageW(ch, 0x000D, n + 1, ct.addressof(buf))  # WM_GETTEXT
                if len(buf.value) > len(best):
                    best = buf.value
        return best

    def focus(self, hwnd):
        u = self.u
        u.keybd_event(0x12, 0, 0, 0)  # Alt를 눌렀다 떼서 윈도우의 포커스 이동 제한을 푼다
        u.keybd_event(0x12, 0, 2, 0)
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        return u.GetForegroundWindow() == hwnd

    def copy_all(self):
        import time
        u = self.u
        for key in (0x41, 0x43):  # A, C
            u.keybd_event(0x11, 0, 0, 0)
            u.keybd_event(key, 0, 0, 0)
            u.keybd_event(key, 0, 2, 0)
            u.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.4)

    def close(self, hwnd):
        self.u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    def diagnose(self):
        lines = []
        if self.proc is not None:
            lines.append(f"notepad.exe 종료코드: {self.proc.poll()} (None=실행 중)")
        wins = self._notepad_windows()
        lines.append(f"메모장으로 보이는 창 {len(wins)}개" + "".join(f"\n  [{c}] {t[:50]}" for _, c, t in wins[:5]))
        return "\n".join(lines)


def _is_complete_step(text: str) -> bool:
    t = text.strip().upper()
    return t.startswith("ISO-10303-21") and "END-ISO-10303-21" in t[-80:]


def grab_text_via_notepad(path, tk_root, log=lambda m: None, backend=None, timeout=15, read_timeout=15):
    """파일을 메모장으로 열어 내용을 가져온 뒤 메모장을 닫습니다. 저장은 하지 않습니다.
    1단계: 메모장 입력칸의 글자를 직접 읽음 / 2단계(예비): Ctrl+A, Ctrl+C 후 클립보드에서 읽음"""
    import time
    if backend is None:
        if sys.platform != "win32":
            raise RuntimeError("이 기능은 윈도우에서만 동작합니다.")
        backend = _WinNotepad()

    before = backend.snapshot()
    log("메모장으로 파일을 여는 중입니다. 끝날 때까지 키보드와 마우스를 건드리지 마세요.")
    backend.launch(path)
    hwnd, end = None, time.time() + timeout
    while time.time() < end:
        hwnd = backend.find_window(Path(path).name, before)
        if hwnd:
            break
        tk_root.update()
        time.sleep(0.1)
    if not hwnd:
        raise RuntimeError("메모장 창을 찾지 못했습니다.\n" + backend.diagnose())

    try:
        log("메모장에서 내용을 읽는 중입니다...")
        text, end = "", time.time() + read_timeout
        while time.time() < end:
            t = backend.read_text(hwnd)
            if t and _is_complete_step(t):
                return t
            tk_root.update()
            time.sleep(0.12)

        log("직접 읽기가 안 되어 복사 방식으로 시도합니다...")
        try:
            prev = tk_root.clipboard_get()
        except Exception:
            prev = ""
        try:
            focused = False
            for _ in range(3):
                if backend.focus(hwnd):
                    focused = True
                    break
                time.sleep(0.5)
            if not focused:
                raise RuntimeError("메모장 창을 앞으로 가져오지 못했습니다.\n" + backend.diagnose())
            backend.copy_all()
            end = time.time() + read_timeout
            while time.time() < end:
                try:
                    t = tk_root.clipboard_get()
                except Exception:
                    t = ""
                if t and _is_complete_step(t):
                    return t
                tk_root.update()
                time.sleep(0.3)
        finally:
            tk_root.clipboard_clear()  # 가져온 내용이 클립보드에 남지 않게 하고, 원래 내용을 되돌림
            if prev:
                tk_root.clipboard_append(prev)
        raise RuntimeError(
            "메모장에서 STEP 내용을 가져오지 못했습니다. 입력칸 직접 읽기와 복사가 모두 실패했습니다.\n"
            "문서 보안이 막았거나, 파일이 아직 다 열리지 않았을 수 있습니다.\n" + backend.diagnose()
        )
    finally:
        backend.close(hwnd)


def read_step_via_notepad(p: Path, clip, log=lambda m: None) -> str:
    """STEP 파일 내용은 항상 '메모장으로 열어 내용을 가져오는' 방식으로 읽는다(처음 정한 방식).
    로컬 파일도 같은 방식. 윈도우가 아닌 환경(개발·자동 점검)에서만 직접 읽는다."""
    if sys.platform != "win32":
        return Path(p).read_bytes().decode("utf-8", errors="replace")
    return grab_text_via_notepad(p, clip, log)


# ---------------------------------------------------------------- 기록 저장
HIST_DIR = APP_DIR / "step_history"
HIST_INDEX = HIST_DIR / "history.json"
HIST_CSV = HIST_DIR / "history.csv"  # 엑셀로 열어볼 수 있는 표


def history_load() -> list:
    import json
    try:
        return json.loads(HIST_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []


def _history_write(items: list):
    import json
    HIST_DIR.mkdir(exist_ok=True)
    HIST_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(HIST_CSV, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글 안 깨짐
        w = csv.writer(f)
        w.writerow(["열어본 시각", "파일명", "부품명", "재질", "X(mm)", "Y(mm)", "Z(mm)",
                    "부피(mm3)", "밀도(g/cm3)", "질량(g)"])
        for e in items:
            w.writerow([e["saved_at"], e["name"], e["part"], e["material"], e["size_x"], e["size_y"],
                        e["size_z"], e["volume_mm3"], e["density"], e["mass_g"]])


def history_add(text: str, info: dict, name: str) -> dict:
    """모델링한 STEP 내용과 정보를 저장. 같은 내용이 이미 있으면 시각만 갱신해 맨 위로 올린다."""
    import hashlib
    import time as _t
    HIST_DIR.mkdir(exist_ok=True)
    sha = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
    items = [e for e in history_load()]
    old = next((e for e in items if e.get("sha1") == sha), None)
    if old:
        items.remove(old)
        stp = old["stp"]
    else:
        stp = f"{sha[:16]}/model.stp"
    target = HIST_DIR / stp
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(text.encode("utf-8", errors="replace"))
    entry = {
        "sha1": sha, "stp": stp, "saved_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "name": name, "part": info.get("part", ""), "material": info.get("material", ""),
        "size_x": info.get("size_x"), "size_y": info.get("size_y"), "size_z": info.get("size_z"),
        "volume_mm3": info.get("volume_mm3"),
        "density": None if info.get("density_g_cm3") is None else round(info["density_g_cm3"], 4),
        "mass_g": info.get("mass_g"),
    }
    items.insert(0, entry)
    _history_write(items)
    return entry


def history_text(entry: dict) -> str:
    return (HIST_DIR / entry["stp"]).read_bytes().decode("utf-8", errors="replace")


def history_delete(sha: str):
    items = history_load()
    for e in items:
        if e.get("sha1") == sha:
            try:
                f = HIST_DIR / e["stp"]
                if f.parent != HIST_DIR:      # 모델별 폴더(원본 STEP + 저장된 메시·정보)를 통째로 삭제
                    import shutil
                    shutil.rmtree(f.parent, ignore_errors=True)
                else:                          # 예전 방식으로 저장된 파일
                    f.unlink()
            except OSError:
                pass
    _history_write([e for e in items if e.get("sha1") != sha])


def model_dir(text: str) -> Path:
    """같은 STEP 내용이면 항상 같은 폴더 → 이미 계산한 결과를 재사용한다."""
    import hashlib
    sha = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
    return HIST_DIR / sha[:16]


def load_cached(d: Path, name: str, default_density: float | None):
    """저장된 info.json + mesh.vtp 를 읽는다. 없으면 None. 질량은 현재 밀도 입력값으로 다시 계산."""
    import json
    import pyvista as pv
    try:
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
        if info.get("cache_ver", 1) < CACHE_VER:
            return None  # 예전 형식 → 다시 계산
        info["_mesh"] = pv.read(str(d / "mesh.vtp"))
    except Exception:
        return None
    info["circle_diameters"] = {float(k): v for k, v in info.get("circle_diameters", [])}
    info["file"] = name
    dens = info.get("file_density") or default_density
    info["density_g_cm3"] = dens
    info["mass_g"] = round(info["volume_mm3"] / 1000.0 * dens, 3) if dens else None
    return info


def _ensure_std_streams():
    """창 모드 exe 에서는 sys.stdin/stdout 이 None 일 수 있어, 부모가 넘겨준 파이프 핸들로 직접 연결한다."""
    if sys.stdin is not None and sys.stdout is not None:
        return
    import io
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = ctypes.c_void_p
        if sys.stdin is None:
            fd = msvcrt.open_osfhandle(k32.GetStdHandle(-10), os.O_RDONLY)
            sys.stdin = io.TextIOWrapper(io.FileIO(fd, "r"), encoding="utf-8")
        if sys.stdout is None:
            fd = msvcrt.open_osfhandle(k32.GetStdHandle(-11), 0)
            sys.stdout = io.TextIOWrapper(io.FileIO(fd, "w"), encoding="utf-8", line_buffering=True)
    except Exception:
        pass


def worker_main():
    """별도 프로세스로 돌면서 무거운 계산(STEP 해석 + 메시 생성)만 담당한다.
    메인 창은 그동안 멈추지 않고 진행 상황을 표시할 수 있다."""
    import json
    import time
    import traceback

    _ensure_std_streams()
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    def send(**kw):
        print("@@" + json.dumps(kw, ensure_ascii=False), flush=True)

    try:
        import numpy  # noqa: F401
        import pyvista  # noqa: F401
        preload_occ()
    except Exception as ex:
        send(kind="warn", msg=repr(ex))
    send(kind="ready")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        jid = None
        try:
            job = json.loads(line)
            jid = job["id"]
            d = Path(job["dir"])
            t0 = time.perf_counter()
            send(kind="stage", job=jid, msg="STEP 해석 중 (형상 변환)")
            text = (d / "model.stp").read_bytes().decode("utf-8", errors="replace")
            info = analyze_text(text, job.get("name", "pasted"), job.get("density"), bool(job.get("full_fix")))
            t1 = time.perf_counter()
            send(kind="stage", job=jid, msg="3D 메시 생성 중 (모든 CPU 코어 사용)")
            params = info.pop("_meshp", None)
            mesh = build_mesh(info.pop("_shape"), params)
            t2 = time.perf_counter()
            mesh.save(str(d / "mesh.vtp"))
            info["circle_diameters"] = [[k, v] for k, v in info["circle_diameters"].items()]
            info["timing"] = {"parse": round(t1 - t0, 1), "mesh": round(t2 - t1, 1), **{"d_" + k: v for k, v in info.get("timing_detail", {}).items()}}
            (d / "info.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            send(kind="done", job=jid)
        except BaseException:
            send(kind="error", job=jid, msg=traceback.format_exc())


LOG_PATH = APP_DIR / "step_viewer_error.log"
_ERROR_DIALOG = None  # 한 창 인터페이스가 켜지면 Qt 오류 창 함수로 바뀜


def show_error_box(title: str, msg: str, parent=None):
    """오류 내용을 팝업 창으로 보여 주고, 같은 내용을 로그 파일에도 남긴다."""
    try:
        import time as _t
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{_t.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{msg}\n")
    except Exception:
        pass
    if _ERROR_DIALOG is not None:  # 한 창(Qt) 인터페이스에서는 Qt 오류 창으로
        try:
            _ERROR_DIALOG(title, msg + f"\n\n(로그 파일: {LOG_PATH})")
            return
        except Exception:
            pass
    try:
        from tkinter import messagebox
        messagebox.showerror(title, msg[-3000:] + f"\n\n(로그 파일: {LOG_PATH})", parent=parent)
    except Exception:
        print(f"[{title}]\n{msg}", file=sys.stderr)


def gui(a):
    """메인 창: [파일 선택 → 자동 모델링] 한 번이면 메모장 열기 → 내용 읽기 → 모델링까지 진행."""
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    root = tk.Tk()
    root.title("STEP 모델링")
    # 더블클릭으로 실행된 경우(단독 모드): 메인 창 없이 로딩 창 → 3D 창만 보여 주고, 3D 창을 닫으면 종료
    single = bool(getattr(a, "gui_open", None))
    if single:
        root.withdraw()

    def quit_if_single():
        if single:
            root.after(50, lambda: on_close())

    import traceback
    import faulthandler
    # 이전 실행이 (오류 창도 못 띄우고) 갑자기 죽었다면 남은 기록을 먼저 보여 준다
    try:
        prev_log = LOG_PATH.with_suffix(".crash.log")
        if prev_log.exists() and prev_log.stat().st_size > 0:
            txt = prev_log.read_text(encoding="utf-8", errors="replace")
            prev_log.write_text("", encoding="utf-8")
            show_error_box("이전 실행이 비정상 종료되었습니다", txt, root)
        crash_f = open(prev_log, "a", encoding="utf-8")
        faulthandler.enable(file=crash_f, all_threads=True)
    except Exception:
        pass

    def on_tk_error(exc, val, tb):
        show_error_box("프로그램 오류", "".join(traceback.format_exception(exc, val, tb)), root)

    root.report_callback_exception = on_tk_error

    def preload():
        try:  # 사용자가 파일을 고르는 동안 무거운 라이브러리를 미리 불러옴
            import numpy  # noqa: F401
            import pyvista  # noqa: F401
        except Exception:
            pass  # 실패해도 실제 사용할 때 다시 불러오며 오류를 보여 줌

    import threading
    threading.Thread(target=preload, daemon=True).start()
    root.geometry("800x760")
    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)

    ttk.Label(
        frm,
        text="[파일 선택 → 자동 모델링]을 누르고 STEP 파일을 고르세요.\n"
             "메모장이 잠깐 열렸다 닫히며 자동으로 모델링됩니다. 진행 중에는 키보드와 마우스를 건드리지 마세요.",
    ).pack(anchor="w")

    bar = ttk.Frame(frm)
    bar.pack(fill="x", pady=(8, 0))
    color_var = tk.StringVar(value=a.color or "")
    dens_var = tk.StringVar(value="" if a.density is None else str(a.density))
    ttk.Label(bar, text="밀도 g/cm³ (파일에 없을 때만):").pack(side="left")
    ttk.Entry(bar, textvariable=dens_var, width=7).pack(side="left", padx=(4, 12))
    # 형상이 깨져 보일 때만 켜는 옵션: STEP 형상 보정을 모두 수행(느림). 켜면 저장된 결과도 무시하고 다시 계산.
    fullfix_var = tk.BooleanVar(value=bool(getattr(a, "full_fix", False)))

    out = scrolledtext.ScrolledText(frm, height=14, state="disabled", font=("Malgun Gothic", 10))
    out.pack(fill="both", expand=True, pady=(8, 0))

    def set_out(msg):
        out.config(state="normal")
        out.delete("1.0", "end")
        out.insert("end", msg)
        out.config(state="disabled")
        root.update_idletasks()

    # ---- 무거운 계산(STEP 해석·메시 생성)은 별도 프로세스에서: 계산 중에도 이 창이 멈추지 않는다
    import json
    import queue
    import subprocess
    import time as _time

    wk = {"proc": None, "q": queue.Queue(), "ready": False, "busy": False, "jid": 0}

    def start_worker():
        try:
            HIST_DIR.mkdir(exist_ok=True)
            flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW: 검은 창이 뜨지 않게
            logf = open(LOG_PATH.with_name("step_worker.log"), "a", encoding="utf-8")
            proc = subprocess.Popen(
                self_command() + ["--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=logf,
                text=True, encoding="utf-8", bufsize=1, creationflags=flags,
            )
        except Exception:
            wk["proc"] = None
            return

        wk["proc"], wk["ready"] = proc, False

        def reader():
            for line in proc.stdout:
                if line.startswith("@@"):
                    try:
                        wk["q"].put(json.loads(line[2:]))
                    except Exception:
                        pass
            wk["q"].put({"kind": "exit"})

        threading.Thread(target=reader, daemon=True).start()

    def finish(info, text, name, from_history, note):
        if not from_history:
            try:  # 저장에 실패해도 모델링은 계속 진행
                history_add(text, info, name)
                refresh_hist()
            except Exception as ex:
                set_out(f"[기록 저장 실패] {ex}")
        src = "직접 선택한 색" if color_var.get() else ("파일에 적힌 색" if info["color"] else "기본 은회색")
        set_out(format_info(info) + f"\n색상      : {color_var.get() or info['color'] or DEFAULT_COLOR} ({src})"
                + (f"\n{note}" if note else "") + "\n\n3D 창을 닫으면 이 창으로 돌아옵니다.")
        try:
            show(info, None, color_var.get() or None)
        except BaseException as ex:  # SystemExit 등으로 프로그램이 같이 닫히는 것도 막는다
            set_out(format_info(info) + f"\n\n[3D 표시 실패] {ex!r}")
            show_error_box("3D 표시 실패", traceback.format_exc(), root)
        finally:
            if single:
                quit_if_single()
            else:
                try:
                    root.deiconify()
                    root.lift()
                except Exception:
                    pass

    def run(text, name="pasted", from_history=False):
        if wk["busy"]:
            return
        try:
            dens = float(dens_var.get()) if dens_var.get().strip() else None
        except ValueError:
            set_out("[실패] 밀도는 숫자로 입력해 주세요. 예: 8.94")
            quit_if_single()
            return

        d = model_dir(text)
        # 1) 이미 계산해 둔 모델이면 계산 없이 바로 연다
        if not fullfix_var.get() and (d / "info.json").exists() and (d / "mesh.vtp").exists():
            info = load_cached(d, name, dens)
            if info is not None:
                finish(info, text, name, from_history, "저장된 결과를 불러와 바로 열었습니다 (계산 생략)")
                return

        d.mkdir(parents=True, exist_ok=True)
        (d / "model.stp").write_bytes(text.encode("utf-8", errors="replace"))

        # 2) 작업 프로세스에 계산을 맡긴다. 프로세스가 없으면 다시 띄워 보고, 그래도 안 되면 이 창에서 직접 계산(창이 잠시 멈춤)
        if wk["proc"] is None or wk["proc"].poll() is not None:
            start_worker()
        proc = wk["proc"]
        if proc is None:
            set_out("읽는 중... (이 창에서 직접 계산하므로 잠시 멈출 수 있습니다)")
            try:
                info = analyze_text(text, name, dens, fullfix_var.get())
            except Exception:
                set_out("[실패] 모델링 중 오류")
                show_error_box("모델링 실패", traceback.format_exc(), root)
                quit_if_single()
                return
            finish(info, text, name, from_history, "")
            return

        wk["jid"] += 1
        jid = wk["jid"]
        try:
            proc.stdin.write(json.dumps({"id": jid, "dir": str(d), "name": name, "density": dens,
                                         "full_fix": bool(fullfix_var.get())},
                                        ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception:
            wk["proc"] = None
            set_out("[실패] 계산 프로세스에 연결하지 못했습니다. 다시 시도해 주세요.")
            if single:
                show_error_box("계산 프로세스 연결 실패", "다시 더블클릭해 주세요.", root)
            quit_if_single()
            return
        wk["busy"] = True
        btn.config(state="disabled")
        set_out(f"{name}\n\n계산 중입니다. 완료되면 3D 창이 열립니다.")
        t0 = _time.perf_counter()
        stage = {"msg": "계산 프로세스 준비 중 (처음 한 번만 몇 초 걸림)" if not wk["ready"] else "대기 중"}

        # 계산하는 동안 '계산 중' 로딩 창 하나만 띄운다 (닫기 버튼 없음, 끝나면 자동으로 닫힘)
        dlg = tk.Toplevel(root)
        dlg.title("계산 중")
        dlg.resizable(False, False)
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)
        if not single:  # 메인 창이 숨겨져 있으면 transient 창도 같이 숨으므로 단독 모드에서는 쓰지 않는다
            try:
                dlg.transient(root)
            except Exception:
                pass
        box = ttk.Frame(dlg, padding=20)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="계산 중입니다...", font=("Malgun Gothic", 13, "bold")).pack()
        stage_lbl = ttk.Label(box, text="", font=("Malgun Gothic", 10))
        stage_lbl.pack(pady=(8, 2))
        ttk.Label(box, text=name, font=("Malgun Gothic", 9)).pack()
        bar_pb = ttk.Progressbar(box, mode="indeterminate", length=320)
        bar_pb.pack(pady=(12, 4))
        bar_pb.start(12)
        try:  # 메인 창 가운데에 배치하고, 계산이 끝날 때까지 다른 조작을 막는다
            root.update_idletasks()
            w, h = 380, 170
            if single:  # 화면 가운데
                x = (dlg.winfo_screenwidth() - w) // 2
                y = (dlg.winfo_screenheight() - h) // 2
            else:      # 메인 창 가운데
                x = root.winfo_rootx() + (root.winfo_width() - w) // 2
                y = root.winfo_rooty() + (root.winfo_height() - h) // 2
            dlg.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")
            dlg.grab_set()
            dlg.lift()
            dlg.focus_force()
        except Exception:
            pass

        def close_dlg():
            try:
                bar_pb.stop()
                dlg.grab_release()
                dlg.destroy()
            except Exception:
                pass

        def end(kind, msg=""):
            close_dlg()
            wk["busy"] = False
            btn.config(state="normal")
            if kind == "done":
                info = load_cached(d, name, dens)
                if info is None:
                    set_out("[실패] 계산 결과 파일을 읽지 못했습니다.")
                    show_error_box("결과 읽기 실패", f"{d}", root)
                    quit_if_single()
                    return
                tm = info.get("timing") or {}
                finish(info, text, name, from_history,
                       f"소요 시간 : 해석 {tm.get('parse', '?')}초 + 메시 {tm.get('mesh', '?')}초 "
                       f"(형상 보정: {'정밀' if info.get('fix_mode') == 'full' else '빠른'} 모드) "
                       f"[해석 내역: STEP 변환 {tm.get('d_import', '?')} / 치수 {tm.get('d_bbox', '?')} / "
                       f"부피 {tm.get('d_volume', '?')} / 기타 {tm.get('d_rest', '?')}]\n"
                       "(같은 모델은 다음부터 저장된 결과로 즉시 열립니다)")
            elif kind == "error":
                set_out("[실패] 모델링 중 오류 (자세한 내용은 팝업 참고)")
                if "No module named 'OCP" in msg or "No module named 'pyvista" in msg:
                    msg = ("CAD/3D 엔진 라이브러리를 찾지 못했습니다.\n"
                           "· exe 배포판이면: 묶을 때 라이브러리가 빠진 것입니다. GitHub 배포판을 받거나 "
                           "step_viewer_exe만들기.bat 최신판으로 다시 만들어 주세요.\n"
                           "· 파이썬으로 실행 중이면: pip install cadquery-ocp pyvista 로 설치해 주세요.\n\n" + msg)
                show_error_box("모델링 실패", msg, root)
                quit_if_single()
            else:
                wk["proc"] = None
                set_out("[실패] 계산 프로세스가 갑자기 종료되었습니다.")
                show_error_box("계산 프로세스 종료", f"자세한 기록: {LOG_PATH.with_name('step_worker.log')}", root)
                quit_if_single()

        def poll():
            while True:
                try:
                    m = wk["q"].get_nowait()
                except queue.Empty:
                    break
                k = m.get("kind")
                if k == "ready":
                    wk["ready"] = True
                elif k == "stage" and m.get("job") == jid:
                    stage["msg"] = m["msg"]
                elif k == "done" and m.get("job") == jid:
                    end("done")
                    return
                elif k == "error" and m.get("job") == jid:
                    end("error", m.get("msg", ""))
                    return
                elif k == "exit":
                    end("exit")
                    return
            el = _time.perf_counter() - t0
            stage_lbl.config(text=f"{stage['msg']}  ({el:.0f}초 경과)")
            root.after(150, poll)

        poll()

    def read_step_file(p: Path) -> str:
        return read_step_via_notepad(p, root, set_out)

    def open_path(p: Path):
        if wk["busy"]:
            return
        try:
            text = read_step_file(p)
        except Exception as ex:
            if not single:
                root.lift()
            set_out(f"[실패] {ex}")
            show_error_box("파일 가져오기 실패", traceback.format_exc(), root)
            quit_if_single()
            return
        if not single:
            root.lift()
        run(text, p.name)

    def auto_run():
        """한 번에: 파일 선택 → (필요하면 메모장으로 열어) 내용 가져오기 → 모델링"""
        if wk["busy"]:
            return
        from tkinter import filedialog
        p = filedialog.askopenfilename(title="STEP 파일 선택", filetypes=[("STEP", "*.stp *.step"), ("모든 파일", "*.*")])
        if not p:
            return
        open_path(Path(p))

    def show_color_label():
        color_lbl.config(text=color_var.get() or "파일 색상 사용")

    def pick_color():
        from tkinter import colorchooser
        res = colorchooser.askcolor(title="모델 색상 선택")
        if res and res[1]:
            color_var.set(res[1])
            show_color_label()

    def reset_color():
        color_var.set("")
        show_color_label()

    btn = ttk.Button(bar, text="파일 선택 → 자동 모델링", command=auto_run)
    btn.pack(side="left", padx=4)
    bar2 = ttk.Frame(frm)
    bar2.pack(fill="x", pady=(6, 0))
    ttk.Label(bar2, text="모델 색상:").pack(side="left")
    color_lbl = ttk.Label(bar2, text="", width=16)
    color_lbl.pack(side="left", padx=4)
    ttk.Button(bar2, text="색상 선택", command=pick_color).pack(side="left", padx=4)
    ttk.Button(bar2, text="파일 색상으로", command=reset_color).pack(side="left", padx=4)
    ttk.Checkbutton(bar2, text="정밀 보정 (형상이 깨져 보일 때만, 느림)", variable=fullfix_var).pack(side="left", padx=(16, 0))
    show_color_label()

    # ---- 이전 기록: 열어본 모델을 자동 저장해 두고, 메모장 없이 다시 연다
    hist_frm = ttk.LabelFrame(frm, text="이전 기록 (더블클릭하면 메모장 없이 바로 다시 모델링)", padding=6)
    hist_frm.pack(fill="both", pady=(8, 0))
    cols = ("time", "name", "size", "mass")
    tree = ttk.Treeview(hist_frm, columns=cols, show="headings", height=6, selectmode="browse")
    for c, t, w in (("time", "열어본 시각", 130), ("name", "파일명", 300),
                    ("size", "치수 X×Y×Z (mm)", 170), ("mass", "질량 (g)", 80)):
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="w")
    sb = ttk.Scrollbar(hist_frm, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    sb.pack(side="left", fill="y")
    hist_items = []

    def refresh_hist():
        hist_items.clear()
        hist_items.extend(history_load())
        tree.delete(*tree.get_children())
        for i, e in enumerate(hist_items):
            size = f"{e['size_x']} × {e['size_y']} × {e['size_z']}"
            mass = "-" if e.get("mass_g") is None else e["mass_g"]
            tree.insert("", "end", iid=str(i), values=(e["saved_at"], e["name"], size, mass))

    def selected_entry():
        sel = tree.selection()
        return hist_items[int(sel[0])] if sel else None

    def open_hist(_evt=None):
        e = selected_entry()
        if not e:
            return
        try:
            text = history_text(e)
        except Exception as ex:
            show_error_box("저장된 파일을 읽지 못했습니다", f"{e['stp']}\n{ex}", root)
            return
        run(text, e["name"], from_history=True)

    def delete_hist():
        from tkinter import messagebox
        e = selected_entry()
        if e and messagebox.askyesno("기록 삭제", f"'{e['name']}' 기록을 삭제할까요?\n(저장된 STEP 사본도 함께 지워집니다)", parent=root):
            history_delete(e["sha1"])
            refresh_hist()

    def open_folder():
        import os
        HIST_DIR.mkdir(exist_ok=True)
        os.startfile(str(HIST_DIR))  # 윈도우 전용 (history.csv 는 엑셀로 열림)

    tree.bind("<Double-1>", open_hist)
    hb = ttk.Frame(frm)
    hb.pack(fill="x", pady=(4, 0))
    ttk.Button(hb, text="선택한 기록 다시 열기", command=open_hist).pack(side="left", padx=2)
    ttk.Button(hb, text="선택한 기록 삭제", command=delete_hist).pack(side="left", padx=2)
    ttk.Button(hb, text="저장 폴더 열기 (엑셀 표 포함)", command=open_folder).pack(side="left", padx=2)
    refresh_hist()
    def on_close():
        try:
            if wk["proc"] is not None:
                wk["proc"].kill()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if getattr(a, "gui_open", None):  # STEP 파일을 더블클릭해서 실행된 경우: 창이 뜨면 바로 그 파일을 연다
        root.after(400, lambda: open_path(Path(a.gui_open)))
    start_worker()  # 파일을 고르는 동안 미리 라이브러리를 불러 두기 위해 창이 뜨자마자 시작
    root.mainloop()
    return 0


# ================================================================ 한 창 인터페이스 (Qt)
# 위쪽 명령 도구 모음 · 왼쪽 부품 정보/이전 기록 패널 · 가운데 3D 화면 · 아래 상태 표시줄
# (PySide6 + pyvistaqt 가 없으면 예전 두 창 방식(tkinter)으로 자동 대체)
QT_STYLE = """
QMainWindow, QWidget#panel { background: #f5f5f7; }
QToolBar#command { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #ececee);
                   border: none; border-bottom: 1px solid #d2d2d7; spacing: 2px; padding: 4px 6px; }
QToolBar#headsup { background: transparent; border: none; spacing: 1px; padding: 2px; }
QToolButton { border: 1px solid transparent; border-radius: 6px; padding: 3px 6px; color: #1d1d1f; }
QToolButton:hover { background: #e8e8ed; }
QToolButton:checked { background: #d6e6ff; border-color: #a9c8f5; }
QToolButton:disabled { color: #a1a1a6; }
QTabWidget::pane { border: 1px solid #d2d2d7; border-radius: 8px; background: #ffffff; top: -1px; }
QTabBar::tab { background: transparent; padding: 5px 12px; color: #6e6e73; border: none; }
QTabBar::tab:selected { color: #1d1d1f; border-bottom: 2px solid #0a84ff; }
QTreeWidget { border: none; background: #ffffff; alternate-background-color: #fafafc; }
QTreeWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected { background: #d6e6ff; color: #1d1d1f; }
QHeaderView::section { background: #f5f5f7; border: none; border-bottom: 1px solid #e5e5ea; padding: 4px; color: #6e6e73; }
QPushButton { background: #ffffff; border: 1px solid #d2d2d7; border-radius: 6px; padding: 4px 10px; }
QPushButton:hover { background: #f0f0f5; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #d2d2d7; border-radius: 6px; padding: 3px 6px; }
QStatusBar { background: #f5f5f7; border-top: 1px solid #d2d2d7; color: #3a3a3c; }
QFrame#viewframe { background: #ffffff; border: 1px solid #d2d2d7; border-radius: 8px; }
QLabel#hint { color: #6e6e73; }
"""


def _qt_icon(kind: str):
    """도구 모음 아이콘을 직접 그린다(외부 이미지 파일 없이). 뷰 방향은 등각 정육면체에 해당 면을 칠한 모양."""
    from qtpy import QtCore, QtGui
    S = 28
    pm = QtGui.QPixmap(S, S)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor("#3a3a3c"), 1.4)
    p.setPen(pen)
    blue, light = QtGui.QColor("#4c8dff"), QtGui.QColor("#e4e9f2")
    P = QtCore.QPointF
    # 등각 정육면체 꼭짓점
    top = [P(14, 3), P(25, 9), P(14, 15), P(3, 9)]
    left = [P(3, 9), P(14, 15), P(14, 26), P(3, 20)]
    right = [P(14, 15), P(25, 9), P(25, 20), P(14, 26)]

    def poly(pts, fill):
        p.setBrush(QtGui.QBrush(fill))
        p.drawPolygon(QtGui.QPolygonF(pts))

    if kind in ("iso", "front", "top", "right", "fit"):
        poly(top, blue if kind in ("top", "iso") else light)
        poly(left, blue if kind in ("front", "iso") else light)
        poly(right, blue if kind in ("right", "iso") else light)
        if kind == "fit":
            p.setPen(QtGui.QPen(QtGui.QColor("#0a84ff"), 2))
            for a, b in (((1, 1), (7, 1)), ((1, 1), (1, 7)), ((27, 27), (21, 27)), ((27, 27), (27, 21))):
                p.drawLine(P(*a), P(*b))
    elif kind in ("shaded_edges", "shaded", "wire"):
        fill = QtGui.QColor("#9fb4cc") if kind != "wire" else QtCore.Qt.transparent
        p.setPen(QtGui.QPen(QtGui.QColor("#1d1d1f" if kind != "shaded" else "#9fb4cc"), 1.4))
        for face in (top, left, right):
            poly(face, fill if not isinstance(fill, QtGui.QColor) else (fill.lighter(115) if face is top else fill))
    elif kind.startswith("sec"):
        poly(top, light); poly(left, light); poly(right, light)
        p.setPen(QtGui.QPen(QtGui.QColor("#d0342c"), 2.2))
        ax = kind[-1]
        if ax == "x":
            p.drawLine(P(8.5, 6), P(8.5, 23))
        elif ax == "y":
            p.drawLine(P(19.5, 6), P(19.5, 23))
        else:
            p.drawLine(P(3, 14.5), P(25, 14.5))
        p.setPen(pen)
        f = p.font(); f.setPointSize(7); f.setBold(True); p.setFont(f)
        p.drawText(QtCore.QRectF(0, 0, S, S), QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom, ax.upper())
    elif kind == "dims":
        p.setPen(QtGui.QPen(QtGui.QColor("#1f4e79"), 1.6))
        p.drawLine(P(4, 18), P(24, 18)); p.drawLine(P(4, 13), P(4, 23)); p.drawLine(P(24, 13), P(24, 23))
        p.setBrush(QtGui.QColor("#1f4e79"))
        p.drawPolygon(QtGui.QPolygonF([P(4, 18), P(8, 16), P(8, 20)]))
        p.drawPolygon(QtGui.QPolygonF([P(24, 18), P(20, 16), P(20, 20)]))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.drawText(QtCore.QRectF(0, 1, S, 12), QtCore.Qt.AlignCenter, "Ø")
    elif kind == "box":
        p.setPen(QtGui.QPen(QtGui.QColor("#6e6e73"), 1.3, QtCore.Qt.DashLine))
        p.setBrush(QtCore.Qt.NoBrush)
        for face in (top, left, right):
            p.drawPolygon(QtGui.QPolygonF(face))
    elif kind == "rotate":
        p.setPen(QtGui.QPen(QtGui.QColor("#0a84ff"), 2.2))
        p.drawArc(QtCore.QRectF(5, 5, 18, 18), 30 * 16, 290 * 16)
        p.setBrush(QtGui.QColor("#0a84ff"))
        p.drawPolygon(QtGui.QPolygonF([P(23, 9), P(18, 10), P(22, 15)]))
    elif kind == "open":
        p.setBrush(QtGui.QColor("#ffd479")); p.setPen(QtGui.QPen(QtGui.QColor("#b8860b"), 1.3))
        p.drawRoundedRect(QtCore.QRectF(3, 8, 22, 15), 2, 2)
        p.drawRoundedRect(QtCore.QRectF(3, 5, 9, 5), 1.5, 1.5)
    elif kind == "panel":
        p.setBrush(QtGui.QColor("#ffffff")); p.drawRoundedRect(QtCore.QRectF(3, 5, 22, 18), 3, 3)
        p.setBrush(QtGui.QColor("#9fb4cc")); p.drawRect(QtCore.QRectF(3.7, 5.7, 7, 16.6))
    elif kind == "color":
        for i, c in enumerate(("#ff6b6b", "#ffd166", "#06d6a0", "#4c8dff")):
            p.setBrush(QtGui.QColor(c)); p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QRectF(4 + (i % 2) * 10, 4 + (i // 2) * 10, 10, 10))
    elif kind == "fix":
        p.setPen(QtGui.QPen(QtGui.QColor("#3a3a3c"), 2))
        p.drawLine(P(7, 21), P(18, 10))
        p.setBrush(QtGui.QColor("#ff9f0a")); p.drawEllipse(QtCore.QRectF(15, 4, 9, 9))
    p.end()
    return QtGui.QIcon(pm)


class _QtClipboard:
    """메모장 읽기 함수가 쓰는 tkinter 클립보드 함수들을 Qt 로 흉내 낸다."""

    def __init__(self, app):
        self.app = app

    def update(self):
        self.app.processEvents()

    def clipboard_get(self):
        return self.app.clipboard().text()

    def clipboard_clear(self):
        self.app.clipboard().clear()

    def clipboard_append(self, t):
        self.app.clipboard().setText(t)


class _Scene:
    """3D 화면에 놓인 모델·모서리·치수·단면·외곽 상자를 관리한다."""

    def __init__(self, pl):
        self.pl = pl
        self.info = None
        self.mesh = None
        self.body = self.edges = self.box = None
        self.dim_names, self.clip_names = [], []
        self.section = None
        self.dims_on, self.box_on, self.style = True, False, "shaded_edges"
        self.color = DEFAULT_COLOR

    def _bg(self):
        self.pl.set_background("#c5d0dd", top="#ffffff")  # 위는 흰색, 아래로 갈수록 연한 청회색

    def load(self, info, color=None):
        import pyvista as pv
        pl = self.pl
        self.section_off()
        pl.clear()
        try:  # clear() 가 조명까지 지워서 다시 켠다 (없으면 음영 없이 납작하게 보임)
            pl.enable_lightkit()
        except Exception:
            pass
        self._bg()
        self.info = info
        self.mesh = info["_mesh"] if info.get("_mesh") is not None else build_mesh(info["_shape"], info.get("_meshp"))
        self.color = color or info.get("color") or DEFAULT_COLOR
        self.body = pl.add_mesh(self.mesh, color=self.color, smooth_shading=False, specular=0.4,
                                split_sharp_edges=True, name="body")
        e = self.mesh.extract_feature_edges(feature_angle=35, boundary_edges=True, non_manifold_edges=False)
        self.edges = pl.add_mesh(e, color="#222222", line_width=1, name="edges") if e.n_points else None
        self.box = pl.add_mesh(pv.Box(bounds=self.mesh.bounds).outline(), color="#8e8e93", line_width=1,
                               name="box_outline", pickable=False)
        try:
            self.dim_names = add_dimensions(pl, info)
        except Exception:
            self.dim_names = []
        self.apply()
        self.view("iso")

    def apply(self):
        if self.body is None:
            return
        self.body.GetProperty().SetRepresentationToWireframe() if self.style == "wire" \
            else self.body.GetProperty().SetRepresentationToSurface()
        if self.edges is not None:
            self.edges.SetVisibility(self.style != "shaded" and self.section is None)
        self.body.SetVisibility(self.section is None)
        self.box.SetVisibility(self.box_on)
        set_dims_visible(self.pl, self.dim_names, self.dims_on)
        self.pl.render()

    def view(self, name):
        if self.body is None:
            return
        pl = self.pl
        {"iso": pl.view_isometric, "front": pl.view_xz, "top": pl.view_xy, "right": pl.view_yz}.get(
            name, pl.view_isometric)()
        if name == "fit":
            pl.reset_camera()
        pl.renderer.reset_camera_clipping_range()
        pl.render()

    def section_off(self):
        try:
            self.pl.clear_plane_widgets()
        except Exception:
            pass
        for nm in self.clip_names:
            try:
                self.pl.remove_actor(nm)
            except Exception:
                pass
        self.clip_names, self.section = [], None

    def set_section(self, axis):
        if self.body is None:
            return
        self.section_off()
        if axis:
            before = set(self.pl.renderer.actors.keys())
            self.pl.add_mesh_clip_plane(self.mesh, normal=axis, crinkle=False, color=self.color,
                                        smooth_shading=False, backface_params={"color": "#c0392b"},
                                        widget_color="#0a84ff", outline_opacity=0.15)
            self.clip_names = [k for k in self.pl.renderer.actors.keys() if k not in before]
            self.section = axis
        self.apply()

    def set_color(self, c):
        self.color = c
        if self.body is not None:
            self.body.GetProperty().SetColor(*[int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)])
            if self.section:
                self.set_section(self.section)
            self.pl.render()


def _fill_info_tree(tree, info, note=""):
    """솔리드웍스 FeatureManager 처럼 부품 정보를 나무 구조로 보여 준다."""
    from qtpy import QtGui, QtWidgets
    tree.clear()
    if not info:
        return
    root = QtWidgets.QTreeWidgetItem([info.get("part") or info.get("file") or "부품", ""])
    f = root.font(0); f.setBold(True); root.setFont(0, f)
    tree.addTopLevelItem(root)

    def add(parent, k, v=""):
        it = QtWidgets.QTreeWidgetItem([k, str(v)])
        it.setToolTip(1, str(v))  # 칸이 좁아 잘린 값은 마우스를 올리면 전체가 보임
        parent.addChild(it)
        return it

    add(root, "파일", info.get("file", ""))
    add(root, "재질", info.get("material") or "-")
    dens = info.get("density_g_cm3")
    add(root, "밀도", f"{dens:g} g/cm³" if dens else "-")
    size = add(root, "외형 치수", f"{_fmt_mm(info['size_x'])} × {_fmt_mm(info['size_y'])} × {_fmt_mm(info['size_z'])} mm")
    for ax in "xyz":
        add(size, ax.upper(), f"{_fmt_mm(info['size_' + ax])} mm")
    add(root, "부피", f"{info['volume_mm3']:,} mm³")
    add(root, "질량", f"{info['mass_g']} g" if info.get("mass_g") is not None else "밀도 정보 없음")
    faces = add(root, "면", f"{info.get('faces', '')}개")
    for k, v in (info.get("face_types") or {}).items():
        add(faces, k, f"{v}")
    holes = hole_groups(info, max_labels=50)
    if holes:
        hd = add(root, "구멍·축 지름", f"{len(holes)}종")
        for i, (d, cnt, _c, _n, _r) in enumerate(holes):
            it = add(hd, ("%d×" % cnt if cnt > 1 else "") + "Ø" + _fmt_mm(d), "클릭하면 3D 에서 강조")
            it.setData(0, 32, i)  # Qt.UserRole: 강조할 구멍 번호
            pm = QtGui.QPixmap(12, 12); pm.fill(QtGui.QColor(HOLE_COLORS[i % len(HOLE_COLORS)]))
            it.setIcon(0, QtGui.QIcon(pm))
    add(root, "작성", info.get("software") or "-")
    if note:
        add(root, "비고", note)
    tree.expandItem(root)
    tree.expandItem(size)
    if holes:
        tree.expandItem(hd)  # 색 견본과 함께 지름 목록을 펼쳐 둔다(누르면 3D 에서 강조)
    tree.resizeColumnToContents(0)


def _qt_env():
    """Qt 바인딩을 PySide6 로 고정 (qtpy·VTK 가 다른 바인딩을 찾다가 실패하지 않게)."""
    os.environ.setdefault("QT_API", "pyside6")
    try:
        import vtkmodules.qt
        vtkmodules.qt.PyQtImpl = "PySide6"
    except Exception:
        pass


def gui_qt(a):
    """한 창 인터페이스. PySide6/pyvistaqt 가 없으면 ImportError 를 올려 예전 방식으로 대체된다."""
    import json
    import queue
    import subprocess
    import threading
    import time as _time
    import traceback

    _qt_env()
    from pyvistaqt import QtInteractor
    from qtpy import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Malgun Gothic" if sys.platform == "win32" else app.font().family(), 9))
    app.setStyleSheet(QT_STYLE)
    single = bool(getattr(a, "gui_open", None))

    win = QtWidgets.QMainWindow()
    win.setWindowTitle("STEP 뷰어")
    win.resize(1360, 860)

    # ---- 오류 창을 Qt 로 띄우도록 연결
    global _ERROR_DIALOG
    _ERROR_DIALOG = lambda title, msg: QtWidgets.QMessageBox.critical(win, title, msg[-3000:])  # noqa: E731

    # ---- 가운데 3D 화면 + 헤드업 보기 도구
    center = QtWidgets.QFrame(); center.setObjectName("viewframe")
    cl = QtWidgets.QVBoxLayout(center); cl.setContentsMargins(6, 4, 6, 6); cl.setSpacing(2)
    headsup = QtWidgets.QToolBar(); headsup.setObjectName("headsup"); headsup.setIconSize(QtCore.QSize(22, 22))
    hrow = QtWidgets.QHBoxLayout(); hrow.addStretch(1); hrow.addWidget(headsup); hrow.addStretch(1)
    cl.addLayout(hrow)
    pl = QtInteractor(center)
    cl.addWidget(pl.interactor, 1)
    pl.add_axes()
    scene = _Scene(pl)
    scene._bg()
    # VTK 자체 키(q/e=창 닫기 등)를 끄고 모든 단축키는 Qt 에서 처리
    try:
        pl.iren.clear_key_event_callbacks()
        pl.iren.interactor.RemoveObservers("CharEvent")
    except Exception:
        pass
    win.setCentralWidget(center)

    # ---- 왼쪽 패널: 부품 정보 / 이전 기록
    panel = QtWidgets.QWidget(); panel.setObjectName("panel")
    pv_l = QtWidgets.QVBoxLayout(panel); pv_l.setContentsMargins(8, 8, 4, 8)
    tabs = QtWidgets.QTabWidget()
    info_tree = QtWidgets.QTreeWidget(); info_tree.setHeaderLabels(["항목", "값"]); info_tree.setAlternatingRowColors(True)
    hist_tree = QtWidgets.QTreeWidget(); hist_tree.setHeaderLabels(["열어본 시각", "파일명", "질량(g)"])
    hist_tree.setRootIsDecorated(False); hist_tree.setAlternatingRowColors(True)
    hist_box = QtWidgets.QWidget(); hb = QtWidgets.QVBoxLayout(hist_box); hb.setContentsMargins(0, 0, 0, 0)
    hb.addWidget(hist_tree, 1)
    hbtn = QtWidgets.QHBoxLayout()
    b_hopen, b_hdel, b_hdir = QtWidgets.QPushButton("다시 열기"), QtWidgets.QPushButton("삭제"), QtWidgets.QPushButton("저장 폴더")
    for b in (b_hopen, b_hdel, b_hdir):
        hbtn.addWidget(b)
    hb.addLayout(hbtn)
    tabs.addTab(info_tree, "부품 정보")

    def on_info_click(item, _col=0):
        # '구멍·축 지름' 아래 항목을 누르면 그 지름만 3D 에서 강조, 다른 항목을 누르면 원래대로
        idx = item.data(0, 32)
        highlight_hole(pl, idx if isinstance(idx, int) else None)

    info_tree.itemClicked.connect(on_info_click)
    tabs.addTab(hist_box, "이전 기록")
    pv_l.addWidget(tabs, 1)
    # 설정 영역
    form = QtWidgets.QFormLayout(); form.setLabelAlignment(QtCore.Qt.AlignRight)
    dens_edit = QtWidgets.QLineEdit("" if a.density is None else str(a.density))
    dens_edit.setPlaceholderText("파일에 밀도가 없을 때만 사용 (예: 7.85)")
    form.addRow("밀도 g/cm³", dens_edit)
    hint = QtWidgets.QLabel("마우스: 왼쪽 드래그 회전 · 휠 확대 · Shift+드래그 이동\n"
                            "단축키: Space 자동회전 · V 정면 · T 윗면 · I 등각 · X 단면 · D 치수 · B 상자")
    hint.setObjectName("hint"); hint.setWordWrap(True)
    pv_l.addLayout(form)
    pv_l.addWidget(hint)
    dock = QtWidgets.QDockWidget("", win); dock.setObjectName("leftdock")
    dock.setTitleBarWidget(QtWidgets.QWidget()); dock.setWidget(panel)
    dock.setFeatures(QtWidgets.QDockWidget.NoDockWidgetFeatures)
    panel.setMinimumWidth(300)
    win.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)

    # ---- 상태 표시줄
    status = win.statusBar()
    prog = QtWidgets.QProgressBar(); prog.setRange(0, 0); prog.setMaximumWidth(160); prog.setVisible(False)
    status.addPermanentWidget(prog)

    def say(msg):
        status.showMessage(msg)
        app.processEvents()

    # ---- 위쪽 명령 도구 모음 (솔리드웍스 CommandManager 처럼 아이콘 아래 글자)
    cmd = QtWidgets.QToolBar("명령"); cmd.setObjectName("command"); cmd.setMovable(False)
    cmd.setIconSize(QtCore.QSize(28, 28)); cmd.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
    win.addToolBar(QtCore.Qt.TopToolBarArea, cmd)

    def act(bar, icon, text, slot=None, checkable=False, tip="", shortcut=None):
        ac = QtWidgets.QAction(_qt_icon(icon), text, win)
        ac.setCheckable(checkable)
        ac.setToolTip(tip or text)
        if slot:
            ac.triggered.connect(slot)
        if shortcut:
            for sc in (shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]):
                s = QtWidgets.QShortcut(QtGui.QKeySequence(sc), win)
                s.setContext(QtCore.Qt.ApplicationShortcut)
                s.activated.connect(ac.trigger)
        bar.addAction(ac)
        return ac

    ac_open = act(cmd, "open", "열기", tip="STEP 파일 선택 → 자동 모델링 (Ctrl+O)", shortcut="Ctrl+O")
    ac_panel = act(cmd, "panel", "패널", checkable=True, tip="왼쪽 정보 패널 보이기/숨기기 (F9)", shortcut="F9")
    ac_panel.setChecked(True)
    ac_panel.toggled.connect(dock.setVisible)
    cmd.addSeparator()
    ac_color = act(cmd, "color", "색상", tip="모델 색상 바꾸기")
    ac_fix = act(cmd, "fix", "정밀 보정", checkable=True,
                 tip="형상이 깨져 보일 때만 켜기: STEP 형상 보정을 모두 수행(느림), 저장된 결과 무시")
    ac_fix.setChecked(bool(getattr(a, "full_fix", False)))

    # 헤드업 보기 도구
    ac_fit = act(headsup, "fit", "", lambda: scene.view("fit"), tip="화면에 맞춤 (F)", shortcut="F")
    ac_iso = act(headsup, "iso", "", lambda: scene.view("iso"), tip="등각 보기 (I)", shortcut="I")
    act(headsup, "front", "", lambda: scene.view("front"), tip="정면 보기 (V)", shortcut="V")
    act(headsup, "top", "", lambda: scene.view("top"), tip="윗면 보기 (T)", shortcut="T")
    act(headsup, "right", "", lambda: scene.view("right"), tip="우측면 보기 (R)", shortcut="R")
    headsup.addSeparator()
    style_grp = QtWidgets.QActionGroup(win); style_grp.setExclusive(True)

    def set_style(s):
        scene.style = s
        scene.apply()

    for key, tip, sc in (("shaded_edges", "음영 + 모서리 표시", None), ("shaded", "음영", "S"), ("wire", "와이어프레임", "W")):
        ac = act(headsup, key, "", lambda _=False, k=key: set_style(k), checkable=True, tip=tip + (f" ({sc})" if sc else ""),
                 shortcut=sc)
        style_grp.addAction(ac)
        ac.setChecked(key == "shaded_edges")
    headsup.addSeparator()
    sec_acts = {}

    def set_section(ax):
        new = None if scene.section == ax else ax
        scene.set_section(new)
        for k, ac in sec_acts.items():
            ac.setChecked(k == new)
        refresh_status()

    for ax in "xyz":
        sec_acts[ax] = act(headsup, "sec" + ax, "", lambda _=False, k=ax: set_section(k), checkable=True,
                           tip=f"{ax.upper()} 단면 보기 (다시 누르면 끔, 파란 평면을 끌어 위치 이동)")
    QtWidgets.QShortcut(QtGui.QKeySequence("X"), win, activated=lambda: set_section(
        {None: "x", "x": "y", "y": "z", "z": None}[scene.section] or scene.section)).setContext(QtCore.Qt.ApplicationShortcut)
    headsup.addSeparator()

    def toggle_dims(on):
        scene.dims_on = on
        scene.apply()

    def toggle_box(on):
        scene.box_on = on
        scene.apply()

    ac_dims = act(headsup, "dims", "", checkable=True, tip="도면식 치수 표기 (D)", shortcut="D")
    ac_dims.setChecked(True); ac_dims.toggled.connect(toggle_dims)
    ac_box = act(headsup, "box", "", checkable=True, tip="외곽 상자 (B)", shortcut="B")
    ac_box.toggled.connect(toggle_box)

    # 자동 회전
    rot = {"speed": 1.0}
    rot_timer = QtCore.QTimer(win); rot_timer.setInterval(30)

    def rot_tick():
        if scene.body is not None:
            pl.camera.Azimuth(rot["speed"])
            pl.renderer.reset_camera_clipping_range()
            pl.render()

    rot_timer.timeout.connect(rot_tick)
    ac_rot = act(headsup, "rotate", "", checkable=True, tip="자동 회전 (Space / A, ↑↓ 속도)", shortcut=["Space", "A"])
    ac_rot.toggled.connect(lambda on: (rot_timer.start() if on else rot_timer.stop(), refresh_status()))

    def speed(dlt):
        rot["speed"] = min(8.0, max(0.2, rot["speed"] + dlt))
        refresh_status()

    for sc, dl in (("Up", 0.5), ("Down", -0.5)):
        QtWidgets.QShortcut(QtGui.QKeySequence(sc), win, activated=lambda d=dl: speed(d)).setContext(
            QtCore.Qt.ApplicationShortcut)

    def refresh_status():
        parts = []
        if ac_rot.isChecked():
            parts.append(f"자동 회전 {rot['speed']:.1f}")
        if scene.section:
            parts.append(f"{scene.section.upper()} 단면")
        if parts:
            say("  ·  ".join(parts))

    def pick_color():
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(scene.color), win, "모델 색상")
        if c.isValid():
            scene.set_color(c.name())
            state["color"] = c.name()

    ac_color.triggered.connect(pick_color)
    state = {"color": a.color}

    # ---- 계산 작업 프로세스 (tk 판과 같은 방식)
    wk = {"proc": None, "q": queue.Queue(), "ready": False, "busy": False, "jid": 0}

    def start_worker():
        try:
            HIST_DIR.mkdir(exist_ok=True)
            flags = 0x08000000 if sys.platform == "win32" else 0
            logf = open(LOG_PATH.with_name("step_worker.log"), "a", encoding="utf-8")
            proc = subprocess.Popen(self_command() + ["--worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=logf, text=True, encoding="utf-8", bufsize=1, creationflags=flags)
        except Exception:
            wk["proc"] = None
            return
        wk["proc"], wk["ready"] = proc, False

        def reader():
            for line in proc.stdout:
                if line.startswith("@@"):
                    try:
                        wk["q"].put(json.loads(line[2:]))
                    except Exception:
                        pass
            wk["q"].put({"kind": "exit"})

        threading.Thread(target=reader, daemon=True).start()

    def density():
        t = dens_edit.text().strip()
        if not t:
            return None
        return float(t)

    def set_busy(on, msg=""):
        wk["busy"] = on
        prog.setVisible(on)
        for ac in (ac_open,):
            ac.setEnabled(not on)
        hist_tree.setEnabled(not on)
        if msg:
            say(msg)

    loading = {"dlg": None}

    def show_loading(name):
        dlg = QtWidgets.QDialog(win, QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        dlg.setModal(True)
        dlg.setStyleSheet("QDialog { background: #ffffff; border: 1px solid #d2d2d7; border-radius: 12px; }")
        v = QtWidgets.QVBoxLayout(dlg); v.setContentsMargins(26, 20, 26, 20)
        t = QtWidgets.QLabel("계산 중입니다..."); f = t.font(); f.setPointSize(12); f.setBold(True); t.setFont(f)
        t.setAlignment(QtCore.Qt.AlignCenter)
        stage = QtWidgets.QLabel(""); stage.setAlignment(QtCore.Qt.AlignCenter)
        nm = QtWidgets.QLabel(name); nm.setObjectName("hint"); nm.setAlignment(QtCore.Qt.AlignCenter)
        bar = QtWidgets.QProgressBar(); bar.setRange(0, 0); bar.setTextVisible(False); bar.setFixedWidth(320)
        for w_ in (t, stage, nm, bar):
            v.addWidget(w_)
        dlg.stage = stage
        dlg.show()
        loading["dlg"] = dlg
        return dlg

    def hide_loading():
        if loading["dlg"] is not None:
            loading["dlg"].close()
            loading["dlg"] = None

    def finish(info, text, name, from_history, note):
        if not from_history:
            try:
                history_add(text, info, name)
                refresh_hist()
            except Exception as ex:
                say(f"기록 저장 실패: {ex}")
        scene.load(info, state["color"])
        for k, ac in sec_acts.items():
            ac.setChecked(False)
        ac_dims.setChecked(True); ac_box.setChecked(False)
        scene.dims_on, scene.box_on = True, False
        scene.apply()
        _fill_info_tree(info_tree, info, note)
        tabs.setCurrentIndex(0)
        win.setWindowTitle(f"STEP 뷰어 — {name}")
        say(note or "완료")

    def fail(title, msg):
        hide_loading()
        set_busy(False, "[실패] " + title)
        show_error_box(title, msg, win)

    def run(text, name="pasted", from_history=False):
        if wk["busy"]:
            return
        try:
            dens = density()
        except ValueError:
            QtWidgets.QMessageBox.warning(win, "밀도", "밀도는 숫자로 입력해 주세요. 예: 8.94")
            return
        d = model_dir(text)
        if not ac_fix.isChecked() and (d / "info.json").exists() and (d / "mesh.vtp").exists():
            info = load_cached(d, name, dens)
            if info is not None:
                finish(info, text, name, from_history, "저장된 결과를 불러와 바로 열었습니다 (계산 생략)")
                return
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.stp").write_bytes(text.encode("utf-8", errors="replace"))
        if wk["proc"] is None or wk["proc"].poll() is not None:
            start_worker()
        proc = wk["proc"]
        if proc is None:  # 작업 프로세스를 못 띄우면 이 창에서 직접 계산
            set_busy(True, "계산 중... (창이 잠시 멈출 수 있습니다)")
            try:
                info = analyze_text(text, name, dens, ac_fix.isChecked())
            except Exception:
                fail("모델링 실패", traceback.format_exc())
                return
            set_busy(False)
            finish(info, text, name, from_history, "")
            return
        wk["jid"] += 1
        jid = wk["jid"]
        try:
            proc.stdin.write(json.dumps({"id": jid, "dir": str(d), "name": name, "density": dens,
                                         "full_fix": ac_fix.isChecked()}, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception:
            wk["proc"] = None
            say("[실패] 계산 프로세스에 연결하지 못했습니다. 다시 시도해 주세요.")
            return
        set_busy(True, f"{name} 계산 중...")
        dlg = show_loading(name)
        t0 = _time.perf_counter()
        stg = {"msg": "계산 프로세스 준비 중 (처음 한 번만 몇 초 걸림)" if not wk["ready"] else "대기 중"}
        timer = QtCore.QTimer(win)

        def end(kind, msg=""):
            timer.stop()
            hide_loading()
            set_busy(False)
            if kind == "done":
                info = load_cached(d, name, dens)
                if info is None:
                    fail("결과 읽기 실패", str(d))
                    return
                tm = info.get("timing") or {}
                finish(info, text, name, from_history,
                       f"소요 {tm.get('parse', '?')}+{tm.get('mesh', '?')}초 (해석+메시, "
                       f"{'정밀' if info.get('fix_mode') == 'full' else '빠른'} 보정) · 다음부터 즉시 열림")
            elif kind == "error":
                if "No module named 'OCP" in msg or "No module named 'pyvista" in msg:
                    msg = "CAD/3D 엔진 라이브러리를 찾지 못했습니다. 배포판을 다시 설치해 주세요.\n\n" + msg
                fail("모델링 실패", msg)
            else:
                wk["proc"] = None
                fail("계산 프로세스 종료", f"자세한 기록: {LOG_PATH.with_name('step_worker.log')}")

        def poll():
            while True:
                try:
                    m = wk["q"].get_nowait()
                except queue.Empty:
                    break
                k = m.get("kind")
                if k == "ready":
                    wk["ready"] = True
                elif k == "stage" and m.get("job") == jid:
                    stg["msg"] = m["msg"]
                elif k == "done" and m.get("job") == jid:
                    return end("done")
                elif k == "error" and m.get("job") == jid:
                    return end("error", m.get("msg", ""))
                elif k == "exit":
                    return end("exit")
            if loading["dlg"] is not None:
                loading["dlg"].stage.setText(f"{stg['msg']}  ({_time.perf_counter() - t0:.0f}초)")

        timer.timeout.connect(poll)
        timer.start(150)

    def read_step_file(p: Path) -> str:
        return read_step_via_notepad(p, _QtClipboard(app), say)

    def open_path(p: Path):
        if wk["busy"]:
            return
        try:
            text = read_step_file(p)
        except Exception:
            win.raise_(); win.activateWindow()
            fail("파일 가져오기 실패", traceback.format_exc())
            return
        win.raise_(); win.activateWindow()
        run(text, p.name)

    def choose_file():
        if wk["busy"]:
            return
        # 예전 버전(두 창 방식)과 같은 파일 선택 창을 쓴다. 경로만 받아 메모장으로 넘기는 흐름은 더블클릭과 같다.
        fn = ""
        try:
            import tkinter as tk
            from tkinter import filedialog
            r = tk.Tk()
            r.withdraw()
            r.attributes("-topmost", True)
            fn = filedialog.askopenfilename(parent=r, title="STEP 파일 선택",
                                            filetypes=[("STEP", "*.stp *.step"), ("모든 파일", "*.*")])
            r.destroy()
        except Exception:
            fn, _ = QtWidgets.QFileDialog.getOpenFileName(win, "STEP 파일 선택", "", "STEP (*.stp *.step);;모든 파일 (*.*)")
        if fn:
            open_path(Path(fn))

    ac_open.triggered.connect(choose_file)

    # ---- 이전 기록
    hist_items = []

    def refresh_hist():
        hist_items.clear(); hist_items.extend(history_load())
        hist_tree.clear()
        for e in hist_items:
            it = QtWidgets.QTreeWidgetItem([e["saved_at"], e["name"], "-" if e.get("mass_g") is None else str(e["mass_g"])])
            it.setToolTip(1, f"{e['size_x']} × {e['size_y']} × {e['size_z']} mm")
            hist_tree.addTopLevelItem(it)
        for i in range(3):
            hist_tree.resizeColumnToContents(i)

    def sel_entry():
        it = hist_tree.currentItem()
        return hist_items[hist_tree.indexOfTopLevelItem(it)] if it else None

    def hist_open(*_):
        e = sel_entry()
        if not e:
            return
        try:
            text = history_text(e)
        except Exception as ex:
            show_error_box("저장된 파일을 읽지 못했습니다", f"{e['stp']}\n{ex}", win)
            return
        run(text, e["name"], from_history=True)

    def hist_del():
        e = sel_entry()
        if e and QtWidgets.QMessageBox.question(win, "기록 삭제", f"'{e['name']}' 기록을 삭제할까요?\n(저장된 STEP 사본도 함께 지워집니다)") \
                == QtWidgets.QMessageBox.Yes:
            history_delete(e["sha1"])
            refresh_hist()

    def hist_dir():
        HIST_DIR.mkdir(exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(HIST_DIR)))

    hist_tree.itemDoubleClicked.connect(hist_open)
    b_hopen.clicked.connect(hist_open); b_hdel.clicked.connect(hist_del); b_hdir.clicked.connect(hist_dir)
    refresh_hist()

    # ---- 시작 / 종료
    def on_close(ev):
        rot_timer.stop()
        try:
            if wk["proc"] is not None:
                wk["proc"].kill()
        except Exception:
            pass
        try:
            pl.close()
        except Exception:
            pass
        ev.accept()

    win.closeEvent = on_close
    _fill_info_tree(info_tree, None)
    say("[열기] 또는 Ctrl+O 로 STEP 파일을 고르세요. STEP 파일을 더블클릭해도 열립니다.")
    win.show()
    start_worker()
    if single:
        QtCore.QTimer.singleShot(300, lambda: open_path(Path(a.gui_open)))
    if getattr(a, "_qt_test", None):  # 자체 점검용: 창을 띄운 뒤 콜백 실행
        QtCore.QTimer.singleShot(200, lambda: a._qt_test(win, scene, run, app))
    return app.exec() if hasattr(app, "exec") else app.exec_()


def clipboard_loop(a):
    print("메모장에서 STEP 파일을 열고 Ctrl+A → Ctrl+C 로 전체 복사한 뒤 Enter 를 누르세요. (종료: q)")
    while True:
        ans = input("> ").strip().lower()
        if ans == "q":
            return 0
        text = get_clipboard_text()
        if not text.strip():
            print("[실패] 클립보드가 비어 있습니다. 메모장에서 다시 복사해 주세요.\n")
            continue
        try:
            info = analyze_text(text, "clipboard", a.density)
        except Exception as ex:
            print(f"[실패] {ex}\n")
            continue
        print(format_info(info))
        show(info, a.png, a.color)
        if a.info or a.png:
            return 0
        print("\n다른 부품: 메모장에서 복사한 뒤 Enter (종료: q)")


# ---------------------------------------------------------------- 파일 연결 (더블클릭으로 열기)
PROGID = "StepViewer.STEP.1"
LEGACY_PROGIDS = ("JAS.StepViewer.1",)  # 예전 버전에서 쓰던 이름: 등록/해제 때 같이 정리
MENU_KEY = "StepViewerOpen"
LEGACY_MENU_KEYS = ("JASStepViewer",)
STEP_EXTS_REG = (".stp", ".step")


def _open_command() -> str:
    if FROZEN:  # exe 배포판
        return f'"{Path(sys.executable).resolve()}" --gui-open "%1"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")  # 검은 콘솔 창 없이 실행
    exe = pyw if pyw.exists() else exe
    return f'"{exe}" "{Path(__file__).resolve()}" --gui-open "%1"'


def _report(title: str, lines: list):
    """콘솔이면 출력, 창 모드 exe(콘솔 없음)면 팝업으로 보여 준다."""
    text = "\n".join(lines)
    if "--quiet" in sys.argv:  # 설치 프로그램이 조용히 등록/해제할 때
        return
    if not FROZEN and sys.stdout is not None:
        print(text)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo(title, text, parent=r)
        r.destroy()
    except Exception:
        pass


def register_assoc() -> int:
    """현재 사용자(HKCU)에만 등록하므로 관리자 권한이 필요 없다."""
    out = []
    import winreg as w
    cmd = _open_command()
    base = r"Software\Classes"

    def setv(path, name, value):
        with w.CreateKey(w.HKEY_CURRENT_USER, path) as k:
            w.SetValueEx(k, name, 0, w.REG_SZ, value)

    # 예전 이름으로 등록된 것이 있으면 정리하되, 그 전에 기억해 둔 원래 연결값은 이어받는다
    legacy_prev = _remove_assoc(LEGACY_PROGIDS, restore=False)
    for ext, v in legacy_prev.items():
        setv(rf"{base}\{PROGID}", f"PrevDefault{ext}", v)
    setv(rf"{base}\{PROGID}", "", "STEP 3D 모델 (STEP 뷰어)")
    setv(rf"{base}\{PROGID}\shell", "", "open")
    setv(rf"{base}\{PROGID}\shell\open", "", "STEP 뷰어로 열기")
    setv(rf"{base}\{PROGID}\shell\open", "FriendlyAppName", "STEP 뷰어")
    setv(rf"{base}\{PROGID}\shell\open\command", "", cmd)
    user_choice = []
    for ext in STEP_EXTS_REG:
        try:  # 원래 연결돼 있던 값을 기억해 두었다가 해제할 때 되돌린다
            with w.OpenKey(w.HKEY_CURRENT_USER, rf"{base}\{ext}") as k:
                prev = w.QueryValueEx(k, "")[0]
            if prev and prev != PROGID:
                setv(rf"{base}\{PROGID}", f"PrevDefault{ext}", prev)
        except OSError:
            pass
        setv(rf"{base}\{ext}", "", PROGID)                      # 기본 연결
        setv(rf"{base}\{ext}\OpenWithProgids", PROGID, "")    # '연결 프로그램' 목록에 표시
        # 오른쪽 클릭 메뉴 'STEP 뷰어로 열기' (기본 앱이 다른 프로그램이어도 항상 사용 가능)
        setv(rf"{base}\SystemFileAssociations\{ext}\shell\{MENU_KEY}", "", "STEP 뷰어로 열기")
        setv(rf"{base}\SystemFileAssociations\{ext}\shell\{MENU_KEY}\command", "", cmd)
        try:
            w.OpenKey(w.HKEY_CURRENT_USER,
                      rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice").Close()
            user_choice.append(ext)
        except OSError:
            pass
    _notify_shell()
    out.append("파일 연결 등록 완료")
    out.append(f"  실행 명령: {cmd}")
    out.append("  - .stp / .step 파일 오른쪽 클릭 → 'STEP 뷰어로 열기' 메뉴가 생겼습니다.")
    out.append("    (윈도우 11은 오른쪽 클릭 → '더 많은 옵션 표시' 안에 있습니다)")
    if user_choice:
        out.append(f"\n  ※ {', '.join(user_choice)} 파일은 이미 다른 프로그램이 '항상 사용'으로 지정돼 있어 윈도우가 우선합니다.")
        out.append("    더블클릭으로 열려면 한 번만 아래처럼 지정해 주세요.")
        out.append("    STEP 파일 오른쪽 클릭 → 연결 프로그램 → 다른 앱 선택 → 'STEP 뷰어'(또는 pythonw / STEPViewer) 선택")
        out.append("    → '항상 이 앱을 사용하여 열기' 체크 → 확인")
    else:
        out.append("  - 이제 STEP 파일을 더블클릭하면 바로 뷰어로 열립니다.")
    _report("파일 연결 등록", out)
    return 0


def _remove_assoc(pids, restore=True) -> dict:
    """pids(현재·예전 이름)로 만든 등록을 지우고, 등록 전 원래 연결값(prev)을 돌려준다.
    restore=True 면 확장자 기본값을 원래 값으로 복구한다."""
    import winreg as w
    base = r"Software\Classes"

    def deltree(path):
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, path, 0, w.KEY_ALL_ACCESS) as k:
                while True:
                    try:
                        sub = w.EnumKey(k, 0)
                    except OSError:
                        break
                    deltree(rf"{path}\{sub}")
            w.DeleteKey(w.HKEY_CURRENT_USER, path)
        except OSError:
            pass

    prev = {}
    for pid in pids:
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, rf"{base}\{pid}") as k:
                for ext in STEP_EXTS_REG:
                    try:
                        prev.setdefault(ext, w.QueryValueEx(k, f"PrevDefault{ext}")[0])
                    except OSError:
                        pass
        except OSError:
            pass
        deltree(rf"{base}\{pid}")
    for ext in STEP_EXTS_REG:
        for mk in (MENU_KEY,) + LEGACY_MENU_KEYS:
            deltree(rf"{base}\SystemFileAssociations\{ext}\shell\{mk}")
        for pid in pids:
            try:
                with w.OpenKey(w.HKEY_CURRENT_USER, rf"{base}\{ext}\OpenWithProgids", 0, w.KEY_ALL_ACCESS) as k:
                    w.DeleteValue(k, pid)
            except OSError:
                pass
        try:
            with w.OpenKey(w.HKEY_CURRENT_USER, rf"{base}\{ext}", 0, w.KEY_ALL_ACCESS) as k:
                if w.QueryValueEx(k, "")[0] in pids:
                    if restore and ext in prev:
                        w.SetValueEx(k, "", 0, w.REG_SZ, prev[ext])  # 원래 값으로 복구
                    else:
                        w.DeleteValue(k, "")
        except OSError:
            pass
    return prev


def unregister_assoc() -> int:
    out = []
    _remove_assoc((PROGID,) + LEGACY_PROGIDS, restore=True)
    _notify_shell()
    out.append("파일 연결 해제 완료 (원래 프로그램으로 되돌아갑니다)")
    _report("파일 연결 해제", out)
    return 0


def _notify_shell():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # 탐색기에 연결 변경 알림
    except Exception:
        pass


def diag(path: Path | None):
    """느린 파일의 원인 분석: 단계별 시간과 형상 구성을 출력한다. 사용: python step_viewer.py --diag [파일]"""
    out = []
    import time as _t
    if path is None:  # 파일을 안 주면 step_history 안에서 가장 큰 STEP 을 고른다
        cands = sorted(HIST_DIR.glob("*/model.stp"), key=lambda q: q.stat().st_size, reverse=True)
        if not cands:
            out.append("step_history 폴더에서 STEP 파일을 찾지 못했습니다. 파일 경로를 직접 적어 주세요.")
            _report("STEP 진단", out)
            return 1
        path = cands[0]
    raw = path.read_bytes()
    out.append(f"진단 파일: {path}\n크기    : {len(raw) / 1e6:.1f} MB")
    text = raw.decode("utf-8", errors="replace")
    out.append(f"STEP 개체 수(대략): {text.count(chr(10))}줄, B_SPLINE_SURFACE {text.upper().count('B_SPLINE_SURFACE')}개, "
          f"ADVANCED_FACE {text.upper().count('ADVANCED_FACE(')}개, MANIFOLD_SOLID_BREP {text.upper().count('MANIFOLD_SOLID_BREP')}개")
    t = _t.perf_counter()
    info = analyze_text(text, path.name, None)
    total = _t.perf_counter() - t
    d = info["timing_detail"]
    out.append(f"\n[해석 {total:.1f}초] STEP 변환 {d['import']} / 치수 {d['bbox']} / 부피 {d['volume']} / 기타 {d['rest']}")
    out.append(f"면 {info['faces']}개: {info['face_types']}")
    t = _t.perf_counter()
    mesh = build_mesh(info["_shape"], info.get("_meshp"))
    out.append(f"[메시 {_t.perf_counter() - t:.1f}초] 삼각형 {mesh.n_cells}개")
    out.append("\n이 출력 전체를 복사해서 알려 주세요.")
    _report("STEP 진단", out)
    return 0


def _selftest_worker(text: str, d: Path):
    """계산 프로세스(--worker)를 실제로 띄워 작업 하나를 끝까지 처리하는지 확인한다.
    (메인 창은 CAD 엔진을 직접 쓰지 않으므로, 이 점검이 없으면 엔진 누락을 놓칠 수 있다)"""
    import json
    import subprocess
    import time as _t
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.stp").write_bytes(text.encode("utf-8"))
    flags = 0x08000000 if sys.platform == "win32" else 0
    proc = subprocess.Popen(self_command() + ["--worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=flags)
    try:
        proc.stdin.write(json.dumps({"id": 1, "dir": str(d), "name": "selftest.stp", "density": 7.85}) + "\n")
        proc.stdin.flush()
        t0 = _t.time()
        for line in proc.stdout:
            if not line.startswith("@@"):
                continue
            m = json.loads(line[2:])
            if m.get("kind") == "warn":
                raise AssertionError("계산 프로세스 준비 실패: " + m.get("msg", ""))
            if m.get("kind") == "error":
                raise AssertionError("계산 프로세스 오류:\n" + m.get("msg", ""))
            if m.get("kind") == "done":
                break
            if _t.time() - t0 > 180:
                raise AssertionError("계산 프로세스 응답 없음")
        else:
            raise AssertionError("계산 프로세스가 결과 없이 종료됨")
        assert (d / "info.json").exists() and (d / "mesh.vtp").exists(), "계산 결과 파일 없음"
    finally:
        try:
            proc.kill()
        except Exception:
            pass


def _selftest_qt(info: dict):
    """한 창 인터페이스를 실제로 띄워 모델을 올리고 단면·치수를 한 번씩 켜 본 뒤 닫는다."""
    try:
        _qt_env()
        from pyvistaqt import QtInteractor
        from qtpy import QtCore, QtWidgets
    except ImportError:
        if FROZEN:
            raise
        return  # Qt 가 없는 설치(파이썬 직접 실행)는 예전 방식으로 동작하므로 건너뜀
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    w = QtWidgets.QMainWindow()
    pl = QtInteractor(w)
    w.setCentralWidget(pl.interactor)
    w.resize(500, 400)
    w.show()
    sc = _Scene(pl)
    sc.load(info)
    sc.set_section("x")
    sc.set_section(None)
    sc.dims_on = False
    sc.apply()
    for _ in range(5):
        app.processEvents()
        pl.render()
    assert sc.body is not None and not sc.clip_names, "Qt 화면 점검 실패"
    pl.close()
    w.close()
    app.processEvents()


def selftest(render: bool = True) -> int:
    """배포판 점검: 작은 테스트 형상으로 STEP 읽기 → 분석 → 메시 → 저장/불러오기 → 3D 창(잠깐 열었다 닫기) → PNG.
    성공하면 0, 실패하면 1. 결과는 exe 옆 step_selftest.log 에 남는다."""
    import time as _t
    import traceback
    log = APP_DIR / "step_selftest.log"
    try:
        import pyvista as pv
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        box = BRepPrimAPI_MakeBox(20.0, 10.0, 5.0).Shape()
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(10, 5, -1), gp_Dir(0, 0, 1)), 3.0, 7.0).Shape()
        shape = BRepAlgoAPI_Cut(box, cyl).Shape()
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.stp"
            w = STEPControl_Writer()
            w.Transfer(shape, STEPControl_AsIs)
            w.Write(str(f))
            text = f.read_text(encoding="utf-8", errors="replace")
            info = analyze_text(text, "selftest.stp", 7.85)
            expect = 20 * 10 * 5 - 3.141592653589793 * 9 * 5
            assert abs(info["volume_mm3"] - expect) < 0.5, f"부피 불일치 {info['volume_mm3']} != {expect:.2f}"
            assert (info["size_x"], info["size_y"], info["size_z"]) == (20.0, 10.0, 5.0), "치수 불일치"
            mesh = build_mesh(info.pop("_shape"), info.pop("_meshp"))
            mesh.save(str(Path(d) / "m.vtp"))
            info["_mesh"] = pv.read(str(Path(d) / "m.vtp"))
            _selftest_worker(text, Path(d) / "wk")
            if not render:
                # 화면(OpenGL)이 없는 빌드 서버용: 3D 엔진 모듈이 빠짐없이 들어 있는지만 확인
                import vtkmodules.vtkInteractionStyle  # noqa: F401
                import vtkmodules.vtkRenderingAnnotation  # noqa: F401
                import vtkmodules.vtkRenderingFreeType  # noqa: F401
                import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
                pl = pv.Plotter(off_screen=True)
                pl.add_mesh(info["_mesh"], color=DEFAULT_COLOR)
                pl.add_mesh(info["_mesh"].extract_feature_edges(35), color="#222222")
                pl.add_text(SHORTCUT_MEMO, position="lower_left", font_size=8)
                pl.add_axes()
                pl.add_bounding_box()
                # 단면·측정·치수 눈금 기능이 쓰는 모듈
                import vtkmodules.vtkInteractionWidgets  # noqa: F401
                assert add_dimensions(pl, info), "치수 표기 실패"
                # 한 창 인터페이스(Qt) 구성 요소가 들어 있는지
                # 한 창 인터페이스(Qt) 구성 요소: exe 배포판에는 반드시 있어야 하고,
                # 파이썬 직접 실행 PC 는 없으면 예전 방식으로 동작하므로 건너뜀
                _qt_env()
                try:
                    import PySide6.QtCore  # noqa: F401  (qtpy 보다 먼저 직접 불러 실제 오류를 드러냄)
                except ImportError:
                    if FROZEN:
                        raise
                else:
                    import PySide6.QtWidgets  # noqa: F401
                    import pyvistaqt  # noqa: F401
                    from qtpy import QtWidgets  # noqa: F401
                    import vtkmodules.qt.QVTKRenderWindowInteractor  # noqa: F401
                log.write_text("OK (no render)\n", encoding="utf-8")
                return 0
            show(info, png=str(Path(d) / "s.png"))
            assert (Path(d) / "s.png").stat().st_size > 1000, "이미지 저장 실패"
            # 대화형 창: 잠깐 열고 몇 번 그린 뒤 닫는다
            pl = pv.Plotter(window_size=(400, 300), title="STEP viewer self test")
            pl.add_mesh(info["_mesh"], color=DEFAULT_COLOR)
            pl.add_text("self test", position="lower_left", font_size=8)
            pl.show(interactive_update=True, auto_close=False)
            # 단면·측정·치수 눈금 기능도 실제 창에서 한 번씩 켜 본다
            pl.add_mesh_clip_plane(info["_mesh"], normal="x", crinkle=False)
            add_dimensions(pl, info)
            _selftest_qt(info)
            for _ in range(10):
                pl.camera.Azimuth(5)
                pl.renderer.reset_camera_clipping_range()
                pl.iren.process_events()
                pl.render()
                _t.sleep(0.03)
            pl.close()
        log.write_text("OK\n", encoding="utf-8")
        return 0
    except BaseException:
        log.write_text("FAIL\n" + traceback.format_exc(), encoding="utf-8")
        return 1


def run_gui(a):
    """한 창(Qt) 인터페이스를 우선 쓰고, PySide6/pyvistaqt 가 없으면 예전 두 창 방식으로."""
    if not getattr(a, "classic", False):
        try:
            _qt_env()
            import pyvistaqt  # noqa: F401
            import qtpy  # noqa: F401
        except Exception:
            pass
        else:
            return gui_qt(a)
    return gui(a)


def main():
    if "--worker" in sys.argv:
        return worker_main()
    if "--selftest" in sys.argv:
        return selftest(render="--no-render" not in sys.argv)
    ap = argparse.ArgumentParser(description="STEP 뷰어 + 자동 정보 추출")
    ap.add_argument("path", nargs="?", help="STEP 파일 또는 폴더 (없으면 클립보드 방식)")
    ap.add_argument("--open", action="store_true", help="파일 선택 창으로 열기")
    ap.add_argument("--clip", action="store_true", help="붙여넣기 창 대신 터미널에서 클립보드 방식으로 실행")
    ap.add_argument("--info", action="store_true", help="창 없이 정보만 출력")
    ap.add_argument("--png", help="창 없이 이미지로 저장")
    ap.add_argument("--csv", help="폴더 일괄 처리 결과를 저장할 CSV 경로")
    ap.add_argument("--color", help="모델 색상을 직접 지정. 예: \"#3366cc\" 또는 steelblue (없으면 파일의 색, 그것도 없으면 은회색)")
    ap.add_argument("--gui-open", help="(파일 더블클릭용) 메인 창을 띄우고 이 파일을 바로 연다")
    ap.add_argument("--register", action="store_true", help=".stp/.step 더블클릭 시 이 뷰어로 열리도록 등록")
    ap.add_argument("--unregister", action="store_true", help="파일 연결 등록 해제")
    ap.add_argument("--classic", action="store_true", help="예전 두 창 방식(tkinter) 인터페이스로 실행")
    ap.add_argument("--quiet", action="store_true", help="등록/해제 결과 창을 띄우지 않음(설치 프로그램용)")
    ap.add_argument("--diag", action="store_true", help="느린 파일의 단계별 시간 분석 (경로 생략 시 step_history 안의 가장 큰 파일)")
    ap.add_argument("--full-fix", action="store_true", help="STEP 형상 보정을 모두 수행(느림). 형상이 깨져 보일 때 사용")
    ap.add_argument("--density", type=float, help="파일에 밀도가 없을 때 쓸 값(g/cm³). 예: 구리 8.94, 강 7.85")
    a = ap.parse_args()

    if a.register:
        return register_assoc()
    if a.unregister:
        return unregister_assoc()
    if a.gui_open:
        return run_gui(a)
    if a.diag:
        return diag(Path(a.path) if a.path else None)
    if not a.path and not a.open:
        return clipboard_loop(a) if a.clip else run_gui(a)

    path = Path(a.path) if a.path else pick_file()
    if path is None:
        print("파일이 선택되지 않았습니다.")
        return 1
    if path.is_dir():
        batch(path, Path(a.csv or "step_summary.csv"), a.density)
        return 0
    print(f"여는 파일: {path}")
    try:
        info = analyze_file(path, a.density, a.full_fix)
    except Exception as ex:
        print(f"\n[열기 실패] {ex}")
        return 1
    print(format_info(info))
    if a.info:
        return 0
    show(info, a.png, a.color)
    return 0


if __name__ == "__main__":
    sys.exit(main())
