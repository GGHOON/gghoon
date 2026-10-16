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
    vdir = dist / "_internal" / "vtkmodules"
    if not vdir.is_dir():
        print("vtkmodules 폴더를 찾지 못했습니다:", vdir)
        return 1
    removed_dir = dist / "_vtk_removed"
    removed_dir.mkdir(exist_ok=True)
    freed = 0
    for f in sorted(vdir.iterdir()):
        if not f.is_file():
            continue
        b = base_name(f.name)
        if not (b.startswith("vtk") or b.startswith("viskores")):
            continue  # 파이썬 보조 파일 등은 건드리지 않음
        if b in KEEP:
            continue
        freed += f.stat().st_size
        shutil.move(str(f), str(removed_dir / f.name))
    print(f"VTK 미사용 파일 정리: {freed / 1e6:.0f} MB 줄임 (옮긴 위치: {removed_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist/STEPViewer")))
