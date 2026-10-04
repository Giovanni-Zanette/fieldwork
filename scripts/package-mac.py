"""Reproducible local Apple Silicon app + DMG build. No account/signing purchase."""
from pathlib import Path
import argparse, hashlib, importlib.metadata, json, os, plistlib, re, shutil, subprocess, sys

ROOT=Path(__file__).resolve().parents[1]
VERSION='1.0.2'
BUILD=ROOT/'build'/'desktop'
RELEASE=ROOT/'release'
APP=RELEASE/'Fieldwork.app'

def run(*args): subprocess.run([str(x) for x in args],cwd=ROOT,check=True)
def copy_license_files(destination):
    destination.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/'LICENSE',destination/'Fieldwork-LICENSE.txt')
    shutil.copytree(ROOT/'desktop'/'licenses',destination/'native-runtime-upstream',dirs_exist_ok=True)
    inventory=[]
    for dist in sorted(importlib.metadata.distributions(),key=lambda x:x.metadata.get('Name','')):
        name=dist.metadata.get('Name','unknown'); version=dist.version
        copied=[]
        for relative in dist.files or []:
            if any(word in relative.name.lower() for word in ('license','licence','copying','notice')) or 'licenses' in relative.parts:
                source=Path(dist.locate_file(relative))
                if source.is_file():
                    # Preserve package-relative license structure and attribution.
                    target=destination/'python-packages'/name/relative
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);copied.append(str(target.relative_to(destination)))
        inventory.append({'name':name,'version':version,'license_files':copied,'license_expression':dist.metadata.get('License-Expression'),'license':dist.metadata.get('License')})
    pylicense=Path(sys.base_prefix)/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'LICENSE.txt'
    if not pylicense.is_file(): raise RuntimeError('Python license missing; cannot package.')
    shutil.copy2(pylicense,destination/'Python-LICENSE.txt')
    (destination/'dependency-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    (destination/'README.txt').write_text('Fieldwork is MIT licensed. The app contains CPython, Flask, pdfplumber/pdfminer, pypdfium2/PDFium, Pillow, openpyxl and their runtime dependencies. Exact versions and collected original notices are included here. Build-tool notices (including PyInstaller bootloader exception) are also retained. Apple Cocoa, WebKit and Vision are macOS system frameworks, not redistributed libraries. This build uses no paid API or downloadable OCR model at runtime.\n')

def make_icon(resources):
    from PIL import Image,ImageDraw
    iconset=BUILD/'Fieldwork.iconset';iconset.mkdir(parents=True,exist_ok=True)
    for size in (16,32,64,128,256,512,1024):
        im=Image.new('RGBA',(1024,1024),(0,0,0,0));d=ImageDraw.Draw(im)
        d.rounded_rectangle((42,42,982,982),radius=220,fill='#315f53')
        d.rounded_rectangle((250,160,774,864),radius=60,fill='#fbfbf5')
        d.rounded_rectangle((312,244,712,294),radius=14,fill='#315f53')
        for y in (370,474,578,682):
            d.rounded_rectangle((312,y,478,y+42),radius=10,fill='#b8ccc2');d.rounded_rectangle((510,y,712,y+42),radius=10,fill='#518372')
        small=im.resize((size,size),Image.Resampling.LANCZOS)
        if size<=512:small.save(iconset/f'icon_{size}x{size}.png')
        if size>=32:small.save(iconset/f'icon_{size//2}x{size//2}@2x.png')
    run('iconutil','-c','icns',iconset,'-o',resources/'Fieldwork.icns')

def binary_audit(app):
    evidence=[]
    for file in app.rglob('*'):
        if file.is_symlink():
            if not file.resolve().is_relative_to(app.resolve()):raise RuntimeError(f'External bundle symlink: {file}')
            continue
        if not file.is_file():continue
        with file.open('rb') as handle: magic=handle.read(4)
        if magic not in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca',b'\xca\xfe\xba\xbf',b'\xbf\xba\xfe\xca'):continue
        arch=subprocess.check_output(['lipo','-archs',str(file)],text=True).strip()
        loads=subprocess.check_output(['otool','-l',str(file)],text=True)
        mins=re.findall(r'\bminos\s+([\d.]+)',loads)+re.findall(r'LC_VERSION_MIN_MACOSX\s+cmdsize\s+\d+\s+version\s+([\d.]+)',loads)
        if 'arm64' not in arch:raise RuntimeError(f'Non-arm64 binary {file}: {arch}')
        if not mins:raise RuntimeError(f'Unknown minimum macOS for {file}')
        if any(tuple((list(map(int,x.split('.')))+[0,0,0])[:3])>(13,0,0) for x in mins):raise RuntimeError(f'Binary requires newer than macOS13: {file}: {mins}')
        libs='\n'.join(subprocess.check_output(['otool','-L',str(file)],text=True).splitlines()[1:])
        if '/opt/homebrew/' in libs or '/Users/' in libs:raise RuntimeError(f'Unbundled development-library reference: {file}')
        evidence.append({'path':str(file.relative_to(app)),'architectures':arch,'minimum_macos':mins})
    return evidence

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app-only',action='store_true');args=parser.parse_args()
    if sys.platform!='darwin' or os.uname().machine!='arm64':raise SystemExit('Build on an Apple Silicon Mac.')
    if sys.version_info[:2]!=(3,12):raise SystemExit('Use scripts/package-mac.sh (managed Python3.12).')
    if not (ROOT/'app.py').exists():raise SystemExit('Backend app.py is not ready.')
    BUILD.mkdir(parents=True,exist_ok=True);RELEASE.mkdir(exist_ok=True)
    run('xcrun','swiftc','-O','-target','arm64-apple-macosx13.0','-framework','Vision','-framework','CoreImage','-framework','ImageIO','desktop/VisionOCR.swift','-o',BUILD/'fieldwork-ocr')
    run('xcrun','swiftc','-O','-target','arm64-apple-macosx13.0','-framework','Cocoa','-framework','WebKit','desktop/Fieldwork.swift','-o',BUILD/'Fieldwork')
    run(sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','fieldwork-server','--target-architecture','arm64','--distpath',BUILD/'dist','--workpath',BUILD/'pyinstaller','--specpath',BUILD,'--add-data',f'{ROOT / "static"}:static','--add-data',f'{ROOT / "samples"}:samples','--collect-all','pypdfium2','--collect-all','pypdfium2_raw',str(ROOT/'app.py'))
    if APP.exists():shutil.rmtree(APP)
    macos=APP/'Contents'/'MacOS';resources=APP/'Contents'/'Resources';macos.mkdir(parents=True);resources.mkdir()
    shutil.copy2(BUILD/'Fieldwork',macos/'Fieldwork');shutil.copy2(BUILD/'fieldwork-ocr',resources/'fieldwork-ocr')
    shutil.copytree(BUILD/'dist'/'fieldwork-server',resources/'backend',symlinks=True)
    copy_license_files(resources/'licenses');make_icon(resources)
    info={'CFBundleName':'Fieldwork','CFBundleDisplayName':'Fieldwork','CFBundleIdentifier':'ai.zanette.fieldwork','CFBundleExecutable':'Fieldwork','CFBundlePackageType':'APPL','CFBundleShortVersionString':VERSION,'CFBundleVersion':'3','LSMinimumSystemVersion':'13.0','NSHighResolutionCapable':True,'CFBundleIconFile':'Fieldwork.icns','NSHumanReadableCopyright':'Copyright © 2026 Giovanni Zanette. MIT license.','NSAppTransportSecurity':{'NSAllowsLocalNetworking':True},'LSApplicationCategoryType':'public.app-category.productivity'}
    with (APP/'Contents'/'Info.plist').open('wb') as out:plistlib.dump(info,out)
    audit=binary_audit(APP)
    (RELEASE/f'Fieldwork-{VERSION}-binary-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    run('codesign','--force','--deep','--sign','-',APP)
    run('codesign','--verify','--deep','--strict','--verbose=2',APP)
    if not args.app_only:
        run(sys.executable, ROOT/'scripts'/'package-installer.py', '--app', APP)
    print(f'Built {APP}; audited {len(audit)} Mach-O binaries; minOS<=13.0, arm64. Ad-hoc signed, not notarized.')
if __name__=='__main__':main()
