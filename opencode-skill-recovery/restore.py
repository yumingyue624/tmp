"""Verify everything before restoring. Default is a dry run; never overwrites different files."""
import argparse
import hashlib
import json
import pathlib
import zipfile

BASE = pathlib.Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--home', type=pathlib.Path, default=pathlib.Path.home())
    ap.add_argument('--workspace', type=pathlib.Path, default=pathlib.Path('D:/workspace'))
    ap.add_argument('--include-managed', action='store_true', help='Also restore plugin/cache/extension snapshots')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--verify-only', action='store_true')
    args = ap.parse_args()
    manifest = json.loads((BASE / 'manifest.json').read_text(encoding='utf-8'))
    archive = BASE / 'skills-snapshot.zip'
    expected = (BASE / 'SHA256SUMS').read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise SystemExit('Archive SHA256 mismatch')
    with zipfile.ZipFile(archive) as z:
        for key, files in manifest['objects'].items():
            for name, checksum in files.items():
                if hashlib.sha256(z.read(f'objects/{key}/{name}')).hexdigest() != checksum:
                    raise SystemExit(f'File checksum mismatch: {key}/{name}')
        print('Archive and all file checksums verified.')
        if args.verify_only:
            return
        planned, conflicts = {}, []
        for entry in manifest['entries']:
            if entry['scope'] == 'reference' or (entry['scope'] == 'managed' and not args.include_managed):
                continue
            anchor = (args.home if entry['anchor'] == 'home' else args.workspace).resolve()
            target = (anchor / entry['relative']).resolve()
            if not target.is_relative_to(anchor):
                raise SystemExit('Invalid destination')
            print(f"{entry['name']} -> {target}")
            for name in manifest['objects'][entry['object']]:
                dest = (target / name).resolve()
                if not dest.is_relative_to(target):
                    raise SystemExit('Invalid member path')
                data = z.read(f"objects/{entry['object']}/{name}")
                if (dest.exists() and dest.read_bytes() != data) or (dest in planned and planned[dest] != data):
                    conflicts.append(str(dest))
                planned[dest] = data
        if conflicts:
            raise SystemExit('No files written. Conflicts:\n'+'\n'.join(conflicts))
        if args.apply:
            for dest, data in planned.items():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        print(f"{'Restored' if args.apply else 'Dry run; would restore'} {len(planned)} files.")


if __name__ == '__main__':
    main()
