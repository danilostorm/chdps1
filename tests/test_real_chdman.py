"""Teste com CHDMAN verdadeiro; skip local caso não esteja instalado."""
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import zipfile

from chdps1 import convert


@unittest.skipUnless(shutil.which('chdman'), 'CHDMAN do MAME não encontrado')
class TestOfficialChdman(unittest.TestCase):
    def test_multitrack_create_verify_and_metadata(self):
        cue = '''FILE "CD (Track 01).bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
FILE "CD (Track 02).bin" BINARY
  TRACK 02 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:00:02
FILE "CD (Track 03).bin" BINARY
  TRACK 03 AUDIO
    INDEX 00 00:00:00
    INDEX 01 00:00:01
'''
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            zip_path = root / 'CD.zip'
            with zipfile.ZipFile(zip_path, 'w') as z:
                z.writestr('CD.cue', cue)
                for track in (1, 2, 3):
                    z.writestr(f'CD (Track {track:02d}).bin', bytes([track]) * (2352 * 300))
            logs = []
            convert(zip_path, root, shutil.which('chdman'), logs.append)
            dest = root / 'CD.chd'
            self.assertGreater(dest.stat().st_size, 0)
            info = '\n'.join(logs)
            numbers = {int(x) for x in re.findall(r'TRACK\s*:\s*(\d+)', info)}
            self.assertEqual(numbers, {1, 2, 3}, info)


if __name__ == '__main__':
    unittest.main()