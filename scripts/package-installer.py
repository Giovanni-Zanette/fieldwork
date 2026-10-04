#!/usr/bin/env python3
"""Style a DMG around an existing signed app, without rebuilding or modifying it."""
from pathlib import Path
import argparse
import hashlib
import json
import plistlib
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def app_manifest(app):
    entries = []
    for path in sorted(app.rglob('*')):
        item = {'path': str(path.relative_to(app)), 'mode': stat.S_IMODE(path.lstat().st_mode)}
        if path.is_symlink():
            item.update(type='symlink', target=str(path.readlink()))
        elif path.is_file():
            item.update(type='file', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            item.update(type='directory')
        entries.append(item)
    return entries



def verify_installer(output, expected_app):
    """Inspect the final read-only artifact, then detach only our mount."""
    attached = plistlib.loads(subprocess.check_output([
        'hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(output)]))
    entities = attached['system-entities']
    volume = next(Path(entity['mount-point']) for entity in entities if 'mount-point' in entity)
    device = next(entity['dev-entry'] for entity in entities if 'dev-entry' in entity)
    try:
        copied_app = volume / 'Fieldwork.app'
        if app_manifest(copied_app) != expected_app:
            raise RuntimeError('Final disk image app differs from the signed source bundle.')
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(copied_app)], check=True)
        if b'/Users/' in (volume / '.DS_Store').read_bytes():
            raise RuntimeError('Finder metadata contains an external developer path.')
    finally:
        subprocess.run(['hdiutil', 'detach', device], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / 'release' / 'Fieldwork.app')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('Build the disk image on macOS.')
    app = args.app.resolve()
    with (app / 'Contents' / 'Info.plist').open('rb') as source:
        version = plistlib.load(source)['CFBundleShortVersionString']
    output = (args.output or ROOT / 'release' / f'Fieldwork-{version}-mac-arm64.dmg').resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error(f'Refusing to replace an existing installer: {output}. Choose --output with a new name.')
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    background = ROOT / 'desktop' / 'installer-background.png'
    if not background.is_file():
        parser.error('Missing installer artwork: desktop/installer-background.png')
    before = app_manifest(app)
    # Verify the copied app after all image metadata has been written. In
    # particular, hide_extensions would attach forbidden FinderInfo to .app.
    mounted_app = None
    def verify_mounted_app(event):
        nonlocal mounted_app
        if event.get('operation') == 'file::add':
            mounted_app = Path(event['file'])
        if event.get('type') == 'operation::finished' and event.get('operation') == 'dsstore::create':
            if mounted_app is None or app_manifest(mounted_app) != before:
                raise RuntimeError('Packaged app differs from the original signed bundle.')
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(mounted_app)], check=True)
    import dmgbuild
    dmgbuild.build_dmg(str(output), f'Install Fieldwork {version}', settings={
        'format': 'UDZO',
        'filesystem': 'HFS+',
        'files': [(str(app), 'Fieldwork.app')],
        'symlinks': {'Applications': '/Applications'},
        'background': str(background),
        'window_rect': ((140, 160), (720, 500)),
        'icon_locations': {'Fieldwork.app': (170, 200), 'Applications': (550, 200)},
        'default_view': 'icon-view',
        'include_icon_view_settings': True,
        'include_list_view_settings': False,
        'show_status_bar': False,
        'show_tab_view': False,
        'show_toolbar': False,
        'show_pathbar': False,
        'show_sidebar': False,
        'show_icon_preview': False,
        'show_item_info': False,
        'arrange_by': None,
        'grid_spacing': 80,
        'scroll_position': (0, 0),
        'label_pos': 'bottom',
        'text_size': 14,
        'icon_size': 128,
    }, callback=verify_mounted_app)
    verify_installer(output, before)
    if app_manifest(app) != before:
        raise RuntimeError('Source app changed during installer packaging.')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.sha256').write_text(f'{digest}  {output.name}\n')
    evidence = {'version': version, 'installer_revision': 2, 'bytes': output.stat().st_size,
                'sha256': digest, 'source_app_unchanged': True, 'final_image_signature_verified': True, 'app_entries': before,
                'layout': {'window': [720, 500], 'app': [170, 200], 'applications': [550, 200], 'icon_size': 128}}
    output.with_suffix('.manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'DMG {output}\nSHA256 {digest}\nExisting signed app unchanged.')


if __name__ == '__main__':
    main()
