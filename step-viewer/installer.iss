; STEP 뷰어 설치 프로그램 (Inno Setup 6)
; 현재 사용자 전용 설치 → 관리자 권한 불필요. GitHub Actions 에서 dist\STEPViewer 를 묶어 만든다.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6C2B0F6E-8C1E-4C77-9B3E-5D7A1F2A9C41}
AppName=STEP 뷰어
AppVersion={#AppVersion}
AppPublisher=GGHOON
DefaultDirName={localappdata}\Programs\STEPViewer
DefaultGroupName=STEP 뷰어
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=STEPViewer-Setup
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\STEPViewer.exe
UninstallDisplayName=STEP 뷰어
WizardStyle=modern

[Languages]
#ifdef UseKorean
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#else
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
Name: "assoc"; Description: ".stp / .step 파일을 더블클릭하면 STEP 뷰어로 열기"; GroupDescription: "파일 연결:"
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "바로가기:"

[Files]
Source: "dist\STEPViewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\STEP 뷰어"; Filename: "{app}\STEPViewer.exe"
Name: "{group}\STEP 뷰어 제거"; Filename: "{uninstallexe}"
Name: "{userdesktop}\STEP 뷰어"; Filename: "{app}\STEPViewer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\STEPViewer.exe"; Parameters: "--register --quiet"; Tasks: assoc; Flags: runhidden waituntilterminated
Filename: "{app}\STEPViewer.exe"; Description: "STEP 뷰어 실행"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\STEPViewer.exe"; Parameters: "--unregister --quiet"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterAssoc"

[UninstallDelete]
; 열어 본 도면 사본(step_history)과 로그도 함께 삭제
Type: filesandordirs; Name: "{app}\step_history"
Type: files; Name: "{app}\*.log"
