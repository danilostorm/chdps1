#!/usr/bin/env python3
"""CHDPS1 - conversor e validador de imagens de CD do PlayStation 1.

Requer o chdman oficial do MAME; não modifica a imagem original.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile

SECTOR_BYTES = {"AUDIO": 2352, "MODE1/2352": 2352, "MODE2/2352": 2352,
                "MODE1/2048": 2048, "MODE2/2336": 2336}
MAX_ARCHIVE_BYTES = 25 * 1024**3
MAX_MEMBERS = 10000
FILE_RE = re.compile(r'^FILE\s+(?:"([^"]+)"|(.*?))\s+(\S+)\s*$', re.I)
TRACK_RE = re.compile(r'^TRACK\s+(\d{1,2})\s+([A-Z0-9/]+)\s*$', re.I)
INDEX_RE = re.compile(r'^INDEX\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})\s*$', re.I)
GAP_RE = re.compile(r'^(?:PRE|POST)GAP\s+(\d{2}):(\d{2}):(\d{2})\s*$', re.I)


class ConversionError(Exception):
    pass


@dataclass
class Track:
    number: int
    kind: str
    filename: str
    indices: dict[int, int] = field(default_factory=dict)
    gap: int = 0


@dataclass
class Cue:
    source: str
    tracks: list[Track]
    references: dict[str, str]
    sizes: dict[str, int]

    @property
    def summary(self):
        return ", ".join(f"{t.number:02d} {t.kind}" for t in self.tracks)


def read_cue(raw: bytes) -> str:
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    for codec in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            pass
    raise ConversionError('O arquivo CUE não possui codificação de texto reconhecida.')


def frames(mm: str, ss: str, ff: str) -> int:
    m, s, f = int(mm), int(ss), int(ff)
    if s >= 60 or f >= 75:
        raise ConversionError(f'Tempo inválido no CUE: {mm}:{ss}:{ff}')
    return (m * 60 + s) * 75 + f


def safe_reference(value: str) -> str:
    value = value.strip().replace('\\', '/')
    parts = PurePosixPath(value)
    if not value or '\x00' in value or parts.is_absolute() or '..' in parts.parts or ':' in parts.parts[0]:
        raise ConversionError(f'Caminho inseguro no CUE: {value!r}')
    return str(parts)


def parse_cue(raw: bytes) -> tuple[list[Track], set[str]]:
    current_file = None
    current_track = None
    tracks = []
    refs: set[str] = set()
    for line_no, line in enumerate(read_cue(raw).splitlines(), 1):
        line = line.strip()
        if not line or line.upper().startswith('REM '):
            continue
        match = FILE_RE.match(line)
        if match:
            current_file = safe_reference(match.group(1) or match.group(2))
            if match.group(3).upper() != 'BINARY':
                raise ConversionError(f'Linha {line_no}: FILE {match.group(3)} não é BIN BINARY; requer análise específica.')
            refs.add(current_file)
            continue
        match = TRACK_RE.match(line)
        if match:
            kind = match.group(2).upper()
            if kind not in SECTOR_BYTES:
                raise ConversionError(f'Linha {line_no}: tipo de track não suportado: {kind}')
            if current_file is None:
                raise ConversionError(f'Linha {line_no}: TRACK antes de FILE')
            number = int(match.group(1))
            if number != len(tracks) + 1:
                raise ConversionError(f'Linha {line_no}: numeração de TRACK não sequencial')
            current_track = Track(number, kind, current_file)
            tracks.append(current_track)
            continue
        match = INDEX_RE.match(line)
        if match:
            if current_track is None:
                raise ConversionError(f'Linha {line_no}: INDEX sem TRACK')
            idx = int(match.group(1))
            if idx in current_track.indices:
                raise ConversionError(f'Linha {line_no}: INDEX {idx:02d} repetido')
            current_track.indices[idx] = frames(*match.groups()[1:])
            continue
        match = GAP_RE.match(line)
        if match and current_track:
            current_track.gap += frames(*match.groups())
        # Outros comandos CUE (FLAGS, TITLE, PERFORMER, ISRC, etc.) são preservados.
    if not tracks:
        raise ConversionError('O CUE não contém nenhuma faixa TRACK.')
    for track in tracks:
        if 1 not in track.indices:
            raise ConversionError(f'Track {track.number}: INDEX 01 ausente')
        if 0 in track.indices and track.indices[0] > track.indices[1]:
            raise ConversionError(f'Track {track.number}: INDEX 00 posterior ao INDEX 01')
    return tracks, refs


def resolve_casefold(paths: dict[str, str], reference: str) -> str:
    """Return canonical member-relative path; prevent ambiguous case collisions."""
    options = [actual for key, actual in paths.items() if key.casefold() == reference.casefold()]
    if len(options) != 1:
        raise ConversionError(f'BIN ausente ou nome ambíguo: {reference}')
    return options[0]


def inspect_cue(path: Path) -> Cue:
    if not path.is_file():
        raise ConversionError(f'CUE não encontrado: {path}')
    tracks, refs = parse_cue(path.read_bytes())
    directory = path.parent
    files = {p.name: p.name for p in directory.iterdir() if p.is_file()}
    resolutions = {}
    sizes = {}
    for ref in refs:
        # Referências de uma mesma pasta são padrão de dumps PS1.
        parts = PurePosixPath(ref).parts
        parent = directory
        for part in parts:
            options = [p for p in parent.iterdir() if p.name.casefold() == part.casefold()]
            if len(options) != 1:
                raise ConversionError(f'BIN não encontrado ou ambíguo: {ref}')
            parent = options[0]
        if not parent.is_file():
            raise ConversionError(f'O arquivo referenciado não é um BIN: {ref}')
        if parent.resolve().is_relative_to(directory.resolve()) is False:
            raise ConversionError(f'BIN fora da pasta do CUE: {ref}')
        resolutions[ref] = parent.relative_to(directory).as_posix()
        sizes[ref] = parent.stat().st_size
    result = Cue(str(path), tracks, resolutions, sizes)
    validate_cue(result)
    if any(ref != actual for ref, actual in resolutions.items()):
        raise ConversionError('Diferença de maiúsculas/minúsculas entre CUE e BIN; corrija os nomes antes de converter.')
    return result


def validate_cue(cue: Cue):
    byfile: dict[str, list[Track]] = {}
    for t in cue.tracks:
        byfile.setdefault(t.filename, []).append(t)
    for name, tracks in byfile.items():
        file_size = cue.sizes[name]
        formats = {SECTOR_BYTES[t.kind] for t in tracks}
        if len(formats) != 1:
            raise ConversionError(f'{name}: formatos de setor mistos no mesmo BIN')
        sector = formats.pop()
        if file_size == 0 or file_size % sector:
            raise ConversionError(f'{name}: tamanho {file_size} não é múltiplo de {sector} bytes')
        total = file_size // sector
        last = -1
        for t in tracks:
            start = t.indices.get(0, t.indices[1])
            if start < last or t.indices[1] >= total:
                raise ConversionError(f'Track {t.number}: índices inválidos para {name} ({total} setores)')
            last = t.indices[1]


def zip_catalog(z: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = z.infolist()
    if len(members) > MAX_MEMBERS or sum(i.file_size for i in members) > MAX_ARCHIVE_BYTES:
        raise ConversionError('ZIP grande demais para processamento seguro (limite 25 GiB / 10.000 arquivos).')
    catalog = {}
    for i in members:
        if i.is_dir():
            continue
        path = PurePosixPath(i.filename.replace('\\', '/'))
        if (path.is_absolute() or '..' in path.parts or '\x00' in i.filename
                or ':' in path.parts[0] or (i.external_attr >> 16 & 0o170000) == 0o120000):
            raise ConversionError(f'ZIP contém caminho inseguro: {i.filename}')
        key = str(path).casefold()
        if key in catalog:
            raise ConversionError(f'ZIP contém nomes duplicados/casing ambíguo: {i.filename}')
        catalog[key] = i
    return catalog


def inspect_zip(path: Path) -> list[Cue]:
    with zipfile.ZipFile(path) as z:
        files = zip_catalog(z)
        cues = [i for i in files.values() if i.filename.lower().endswith('.cue')]
        if not cues:
            raise ConversionError('ZIP sem arquivo .cue: não há como garantir as tracks do PS1.')
        result = []
        for entry in cues:
            tracks, refs = parse_cue(z.read(entry))
            parent = PurePosixPath(entry.filename.replace('\\', '/')).parent
            resolutions = {}
            sizes = {}
            for ref in refs:
                member = str(parent / ref).lstrip('./')
                info = files.get(member.casefold())
                if info is None:
                    raise ConversionError(f'{entry.filename}: BIN ausente: {ref}')
                resolutions[ref] = info.filename
                sizes[ref] = info.file_size
            cue = Cue(entry.filename, tracks, resolutions, sizes)
            validate_cue(cue)
            result.append(cue)
        return result


def inspect(path: Path) -> list[Cue]:
    if path.suffix.lower() == '.zip':
        return inspect_zip(path)
    if path.suffix.lower() == '.cue':
        return [inspect_cue(path)]
    raise ConversionError('Escolha um arquivo .cue ou .zip contendo CUE + BINs.')


def find_chdman(explicit: str | None = None) -> str:
    candidates = [explicit, os.getenv('CHDMAN')]
    locations = [Path(sys.executable).parent, Path(__file__).resolve().parent]
    for folder in locations:
        candidates.extend([str(folder / 'chdman.exe'), str(folder / 'chdman')])
    candidates.append(shutil.which('chdman'))
    for value in candidates:
        if value and (shutil.which(value) or Path(value).is_file()):
            return str(shutil.which(value) or Path(value).resolve())
    raise ConversionError('CHDMAN não encontrado. Instale o chdman do MAME ou selecione --chdman CAMINHO.')


def run(command: list[str], log):
    log('$ ' + ' '.join(f'"{x}"' if ' ' in x else x for x in command))
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors='replace', bufsize=1, creationflags=flags) as p:
        lines = []
        for line in p.stdout:
            line = line.rstrip('\r\n')
            if line:
                log(line)
                lines.append(line)
        code = p.wait()
    if code != 0:
        raise ConversionError(f'CHDMAN falhou (código {code}): {command[1]}')
    return '\n'.join(lines)


@contextmanager
def prepared_source(source: Path, cue: Cue):
    """For ZIP files, extract the CUE and only its referenced BINs to a safe temp dir."""
    if source.suffix.lower() == '.cue':
        yield source
        return
    with tempfile.TemporaryDirectory(prefix='chdps1-') as temp:
        root = Path(temp)
        with zipfile.ZipFile(source) as z:
            files = zip_catalog(z)
            cue_info = files[cue.source.casefold()]
            names = {cue.source, *cue.references.values()}
            for name in names:
                entry = files[name.casefold()]
                dst = root / PurePosixPath(entry.filename.replace('\\', '/'))
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(entry) as inp, dst.open('wb') as out:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
            cue_path = root / PurePosixPath(cue_info.filename.replace('\\', '/'))
            # Reescrever apenas na área temporária, se o ZIP variar o case dos arquivos.
            raw = read_cue(cue_path.read_bytes())
            for ref, actual in cue.references.items():
                normalized = PurePosixPath(actual).relative_to(PurePosixPath(cue.source).parent).as_posix()
                if ref != normalized:
                    raw = re.sub(r'(?im)^(FILE\s+")' + re.escape(ref) + r'("\s+BINARY\s*)$',
                                 lambda m: m.group(1) + normalized + m.group(2), raw)
            cue_path.write_text(raw, encoding='utf-8')
            yield cue_path


def convert(source: Path, destination: Path, chdman: str, log=print, overwrite=False):
    cues = inspect(source)
    destination.mkdir(parents=True, exist_ok=True)
    for cue in cues:
        log(f'CUE: {cue.source} | {len(cue.tracks)} tracks | {cue.summary}')
        basename = Path(cue.source).stem
        target = destination / (basename + '.chd')
        if target.exists() and not overwrite:
            raise ConversionError(f'CHD já existe; não será sobrescrito: {target}')
        staged = target.with_name(f'.{target.stem}.{uuid.uuid4().hex}.partial.chd')
        try:
            with prepared_source(source, cue) as cue_path:
                run([chdman, 'createcd', '-i', str(cue_path), '-o', str(staged)], log)
            if not staged.is_file() or staged.stat().st_size == 0:
                raise ConversionError('CHDMAN não gerou um arquivo CHD válido.')
            run([chdman, 'verify', '-i', str(staged)], log)
            details = run([chdman, 'info', '-i', str(staged)], log)
            tracks = {int(n) for n in re.findall(r'\bTRACK\s*:\s*(\d+)\b', details, re.I)}
            expected = set(range(1, len(cue.tracks) + 1))
            if tracks and tracks != expected:
                raise ConversionError(f'Metadados de tracks não conferem: esperado {expected}, encontrado {tracks}')
            if not tracks:
                log('AVISO: esta versão do chdman não exibiu as tracks no info; verificação de contagem inconclusiva.')
            os.replace(staged, target)
            log(f'OK: {target} ({target.stat().st_size:,} bytes)')
        finally:
            staged.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='CHDPS1 — CUE/BIN/ZIP para CHD com checagem de tracks')
    parser.add_argument('source', nargs='?', help='ROM .cue ou .zip; sem argumento abre a interface')
    parser.add_argument('-o', '--output', help='Pasta dos CHDs (padrão: ao lado da ROM)')
    parser.add_argument('--chdman', help='Caminho de chdman.exe/chdman')
    parser.add_argument('--analyze', action='store_true', help='Confere tracks e BINs sem converter')
    parser.add_argument('--overwrite', action='store_true', help='Substitui CHDs após validar o resultado')
    args = parser.parse_args(argv)
    if not args.source:
        launch_gui()
        return 0
    src = Path(args.source).expanduser().resolve()
    try:
        cues = inspect(src)
        for cue in cues:
            print(f'{cue.source}: {len(cue.tracks)} tracks -> {cue.summary}')
            for track in cue.tracks:
                print(f'  {track.number:02d} {track.kind:<11} {track.filename} '
                      f'({cue.sizes[track.filename]:,} bytes) INDEX {track.indices}')
        if not args.analyze:
            convert(src, Path(args.output).expanduser().resolve() if args.output else src.parent,
                    find_chdman(args.chdman), overwrite=args.overwrite)
    except (ConversionError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        print(f'ERRO: {exc}', file=sys.stderr)
        return 1
    return 0


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
    root = tk.Tk()
    root.title('CHDPS1 • Conversor seguro de ROMs PS1')
    root.geometry('790x590')
    root.minsize(650, 500)
    root.configure(bg='#131b2b')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background='#131b2b')
    style.configure('TLabel', background='#131b2b', foreground='#e5eefa', font=('Segoe UI', 10))
    style.configure('TButton', font=('Segoe UI', 10), padding=8)
    panel = ttk.Frame(root, padding=20)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='CHDPS1  •  PlayStation CD Converter', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
    ttk.Label(panel, text='CUE + múltiplos BINs → um CHD. Aceita ZIP diretamente. Sem apagar a ROM original.').pack(anchor='w', pady=(3, 20))
    selected = tk.StringVar()
    output = tk.StringVar()
    tool = tk.StringVar(value=os.environ.get('CHDMAN', ''))
    overwrite = tk.BooleanVar(value=False)
    for caption, variable, kind in [('ROM (.cue ou .zip)', selected, 'file'), ('Salvar CHD na pasta', output, 'folder'), ('CHDMAN (opcional se estiver no PATH)', tool, 'tool')]:
        ttk.Label(panel, text=caption).pack(anchor='w', pady=(8, 3))
        line = ttk.Frame(panel)
        line.pack(fill='x')
        ttk.Entry(line, textvariable=variable).pack(side='left', expand=True, fill='x', padx=(0, 6))
        def pick(v=variable, k=kind):
            val = (filedialog.askdirectory() if k == 'folder' else
                   filedialog.askopenfilename(filetypes=[('ROMs PS1', '*.cue *.zip')] if k == 'file' else [('Executáveis', '*.exe *')]))
            if val:
                v.set(val)
                if k == 'file' and not output.get():
                    output.set(str(Path(val).parent))
        ttk.Button(line, text='Procurar', command=pick).pack(side='right')
    ttk.Checkbutton(panel, text='Permitir substituir CHD existente (após verificação)', variable=overwrite).pack(anchor='w', pady=(12, 8))
    commands = ttk.Frame(panel)
    commands.pack(fill='x')
    progress = ttk.Progressbar(panel, mode='indeterminate')
    progress.pack(fill='x', pady=(13, 8))
    logs = scrolledtext.ScrolledText(panel, height=12, bg='#0a1120', fg='#daf0fc', insertbackground='#ffffff', font=('Consolas', 9))
    logs.pack(fill='both', expand=True)
    events = queue.Queue()
    def start(analyze):
        if not selected.get():
            messagebox.showerror('Selecione a ROM', 'Escolha primeiro um arquivo .cue ou .zip.')
            return
        for btn in buttons:
            btn.config(state='disabled')
        progress.start(12)
        logs.delete('1.0', 'end')
        def work():
            try:
                src = Path(selected.get()).expanduser().resolve()
                cues = inspect(src)
                for cue in cues:
                    events.put('ROM: ' + cue.source + '\n' + cue.summary)
                if not analyze:
                    toolpath = find_chdman(tool.get().strip() or None)
                    dest = Path(output.get()).expanduser().resolve() if output.get() else src.parent
                    convert(src, dest, toolpath, events.put, overwrite.get())
                events.put('✓ Concluído: análise válida.' if analyze else '✓ CHD criado e verificado.')
            except Exception as exc:
                events.put('✗ ERRO: ' + str(exc))
            finally:
                events.put(None)
        threading.Thread(target=work, daemon=True).start()
    buttons = [ttk.Button(commands, text='Analisar tracks', command=lambda: start(True)),
               ttk.Button(commands, text='Converter e verificar', command=lambda: start(False))]
    for b in buttons:
        b.pack(side='left', padx=(0, 10))
    def poll():
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            if event is None:
                progress.stop()
                for btn in buttons:
                    btn.config(state='normal')
            else:
                logs.insert('end', event + '\n')
                logs.see('end')
        root.after(120, poll)
    poll()
    root.mainloop()


if __name__ == '__main__':
    sys.exit(main())