import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from chdps1 import ConversionError, inspect, parse_cue, convert

CUE = b'''FILE "game (Track 01).bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
FILE "game (Track 02).bin" BINARY
  TRACK 02 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:00:02
FILE "game (Track 03).bin" BINARY
  TRACK 03 AUDIO
    INDEX 01 00:00:00
'''


class TestCue(unittest.TestCase):
    def test_multitrack_parse(self):
        tracks, refs = parse_cue(CUE)
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[1].indices, {0: 0, 1: 2})
        self.assertEqual(len(refs), 3)

    def test_bad_index(self):
        with self.assertRaises(ConversionError):
            parse_cue(CUE.replace(b'00:00:02', b'00:00:75'))

    def test_no_missing_track(self):
        with self.assertRaises(ConversionError):
            parse_cue(CUE.replace(b'TRACK 03', b'TRACK 05'))

    def test_path_traversal(self):
        with self.assertRaises(ConversionError):
            parse_cue(CUE.replace(b'game (Track 01).bin', b'../private.bin'))

    @unittest.skipIf(os.name == "nt", "Este mock usa shebang POSIX; Windows é testado pelos demais casos")
    def test_zip_analysis_and_conversion_mock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'game.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('roms/game.cue', CUE)
                for num in range(1, 4):
                    z.writestr(f'roms/game (Track {num:02d}).bin', bytes([num]) * (2352 * 4))
            self.assertEqual(len(inspect(source)[0].tracks), 3)
            fake = root / 'chdman'
            fake.write_text('''#!/usr/bin/env python3
import pathlib, sys
mode, *args = sys.argv[1:]
if mode == 'createcd':
    pathlib.Path(args[args.index('-o')+1]).write_bytes(b'MComprHDfake')
elif mode == 'info':
    print('TRACK:1 TYPE:MODE2_RAW\\nTRACK:2 TYPE:AUDIO\\nTRACK:3 TYPE:AUDIO')
elif mode == 'verify':
    print('verification successful')
else:
    sys.exit(1)
''')
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            convert(source, root / 'output', str(fake), lambda msg: None)
            self.assertEqual((root / 'output' / 'game.chd').read_bytes(), b'MComprHDfake')
            with self.assertRaises(ConversionError):
                convert(source, root / 'output', str(fake), lambda msg: None)

    def test_zip_missing_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'game.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('game.cue', CUE)
                z.writestr('game (Track 01).bin', b'0' * 2352)
            with self.assertRaises(ConversionError):
                inspect(source)

    def test_zip_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'game.zip'
            with zipfile.ZipFile(source, 'w') as z:
                z.writestr('../evil.txt', 'bad')
                z.writestr('game.cue', CUE)
            with self.assertRaises(ConversionError):
                inspect(source)

    def test_bad_sector_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'game.cue').write_bytes(CUE)
            for num in range(1, 4):
                (root / f'game (Track {num:02d}).bin').write_bytes(b'123')
            with self.assertRaises(ConversionError):
                inspect(root / 'game.cue')


if __name__ == '__main__':
    unittest.main()