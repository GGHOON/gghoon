"""exe 묶음 안의 VTK(3D 화면 엔진) 파일 중 이 프로그램이 쓰지 않는 것을 지워 용량을 줄인다.
사용: python prune_vtk.py dist\\STEPViewer   (빌드 배치파일이 자동으로 실행)
지운 파일은 _vtk_removed 폴더로 옮겨 두므로, 문제가 있으면 되돌릴 수 있다."""
import re
import shutil
import sys
from pathlib import Path

# 실제 사용 경로(계산·저장 결과 로드·3D 창·자동회전·단축키·PNG 저장)를 돌려 수집한 목록
KEEP = set("""
vtkChartsCore
vtkChartsCore.so
vtkCommonColor
vtkCommonColor.so
vtkCommonComputationalGeometry
vtkCommonComputationalGeometry.so
vtkCommonCore
vtkCommonCore.so
vtkCommonDataModel
vtkCommonDataModel.so
vtkCommonExecutionModel
vtkCommonExecutionModel.so
vtkCommonMath
vtkCommonMath.so
vtkCommonMisc
vtkCommonMisc.so
vtkCommonSystem
vtkCommonSystem.so
vtkCommonTransforms
vtkCommonTransforms.so
vtkDICOMParser
vtkDICOMParser.so
vtkFiltersCellGrid
vtkFiltersCellGrid.so
vtkFiltersCore
vtkFiltersCore.so
vtkFiltersExtraction
vtkFiltersExtraction.so
vtkFiltersGeneral
vtkFiltersGeneral.so
vtkFiltersGeometry
vtkFiltersGeometry.so
vtkFiltersHybrid
vtkFiltersHybrid.so
vtkFiltersHyperTree
vtkFiltersHyperTree.so
vtkFiltersModeling
vtkFiltersModeling.so
vtkFiltersParallel
vtkFiltersPython
vtkFiltersPython.so
vtkFiltersReduction
vtkFiltersReduction.so
vtkFiltersSources
vtkFiltersSources.so
vtkFiltersStatistics
vtkFiltersStatistics.so
vtkFiltersTexture
vtkFiltersTexture.so
vtkFiltersVerdict
vtkFiltersVerdict.so
vtkIOCellGrid
vtkIOCellGrid.so
vtkIOCore
vtkIOCore.so
vtkIOExport
vtkIOGeometry
vtkIOGeometry.so
vtkIOImage
vtkIOImage.so
vtkIOLegacy
vtkIOLegacy.so
vtkIOXML
vtkIOXML.so
vtkIOXMLParser
vtkIOXMLParser.so
vtkImagingColor
vtkImagingColor.so
vtkImagingCore
vtkImagingCore.so
vtkImagingGeneral
vtkImagingGeneral.so
vtkImagingHybrid
vtkImagingHybrid.so
vtkImagingMath
vtkImagingMath.so
vtkImagingSources
vtkImagingSources.so
vtkInfovisCore
vtkInfovisCore.so
vtkInteractionStyle
vtkInteractionStyle.so
vtkInteractionWidgets
vtkInteractionWidgets.so
vtkParallelCore
vtkParallelCore.so
vtkParallelDIY
vtkParallelDIY.so
vtkPythonContext2D
vtkPythonContext2D.so
vtkRenderingAnnotation
vtkRenderingAnnotation.so
vtkRenderingContext2D
vtkRenderingContext2D.so
vtkRenderingContextOpenGL2
vtkRenderingContextOpenGL2.so
vtkRenderingCore
vtkRenderingCore.so
vtkRenderingFreeType
vtkRenderingFreeType.so
vtkRenderingHyperTreeGrid
vtkRenderingHyperTreeGrid.so
vtkRenderingLOD
vtkRenderingLabel
vtkRenderingMatplotlib
vtkRenderingMatplotlib.so
vtkRenderingOpenGL2
vtkRenderingOpenGL2.so
vtkRenderingSceneGraph
vtkRenderingUI
vtkRenderingUI.so
vtkRenderingVolume
vtkRenderingVolume.so
vtkRenderingVolumeOpenGL2
vtkRenderingVolumeOpenGL2.so
vtkRenderingVtkJS
vtkSerializationManager
vtkViewsContext2D
vtkViewsContext2D.so
vtkViewsCore
vtkViewsCore.so
vtkWebCore
vtkWrappingPythonCore3
vtkWrappingPythonCore3.13.so
vtkdoubleconversion
vtkexpat
vtkexpat.so
vtkfmt
vtkfmt.so
vtkfreetype
vtkfreetype.so
vtkglad
vtkglad.so
vtkjpeg
vtkjpeg.so
vtkjsoncpp
vtkkissfft
vtkkissfft.so
vtkloguru
vtkloguru.so
vtklz4
vtklz4.so
vtklzma
vtklzma.so
vtkmetaio
vtkmetaio.so
vtkpng
vtkpng.so
vtkpugixml
vtkpugixml.so
vtkscn
vtkscn.so
vtksys
vtksys.so
vtktiff
vtktiff.so
vtktoken
vtktoken.so
vtkverdict
vtkverdict.so
vtkx11
vtkx11.so
vtkzlib
vtkzlib.so
""".split())


def base_name(fn: str) -> str:
    n = fn
    if n.startswith("lib"):
        n = n[3:]
    n = re.split(r"\.(cpython|cp\d+)", n)[0]          # 파이썬 확장 모듈 vtkXXX.cp314-win_amd64.pyd
    n = re.sub(r"-\d+\.\d+.*$", "", n)                  # 라이브러리 vtkXXX-9.6.dll / libvtkXXX-9.6.so
    n = n.split(".")[0]
    return n


def main(dist: Path):
    # 리눅스 묶음은 vtkmodules 안에, 윈도우 묶음은 vtk.libs 안에 실제 DLL 이 들어 있다
    dirs = [d for d in (dist / "_internal" / "vtkmodules", dist / "_internal" / "vtk.libs") if d.is_dir()]
    if not dirs:
        print("vtkmodules / vtk.libs 폴더를 찾지 못했습니다:", dist)
        return 1
    removed_root = dist / "_vtk_removed"
    freed = 0
    for vdir in dirs:
        removed_dir = removed_root / vdir.name  # 되돌릴 때 원래 폴더로 돌려놓을 수 있게 폴더별로 보관
        removed_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(vdir.iterdir()):
            if not f.is_file():
                continue
            b = base_name(f.name)
            if not (b.startswith("vtk") or b.startswith("viskores")):
                continue  # 파이썬 보조 파일, msvcp140 같은 런타임은 건드리지 않음
            if b in KEEP:
                continue
            freed += f.stat().st_size
            shutil.move(str(f), str(removed_dir / f.name))
    print(f"VTK 미사용 파일 정리: {freed / 1e6:.0f} MB 줄임 (옮긴 위치: {removed_root})")
    return 0


def restore(dist: Path):
    """정리했던 파일을 원래 자리로 되돌린다."""
    root = dist / "_vtk_removed"
    for sub in (list(root.iterdir()) if root.is_dir() else []):
        if sub.is_file():  # 예전 형식(폴더 구분 없이 보관)은 vtkmodules 로
            shutil.move(str(sub), str(dist / "_internal" / "vtkmodules" / sub.name))
            continue
        target = dist / "_internal" / sub.name
        for f in sub.iterdir():
            shutil.move(str(f), str(target / f.name))
    shutil.rmtree(root, ignore_errors=True)
    print("정리했던 VTK 파일을 되돌렸습니다.")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--restore"]
    target = Path(args[0] if args else "dist/STEPViewer")
    sys.exit(restore(target) if "--restore" in sys.argv else main(target))
