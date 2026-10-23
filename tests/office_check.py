"""Verify Office files in LibreOffice after running browser_check.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.vendor')]

import subprocess
from pypdf import PdfReader


def main():
    output = ROOT / 'test-output'
    soffice = Path(r'C:\Program Files\LibreOffice\program\soffice.exe')
    if not soffice.is_file():
        raise SystemExit('LibreOffice is required for this optional rendering check.')
    original = PdfReader(output / 'browser-timetable.pdf')
    for fmt in ('docx', 'xlsx'):
        destination = output / ('rendered-' + fmt)
        subprocess.run([str(soffice), '-env:UserInstallation=' + (output / 'lo-profile').as_uri(),
                        '--headless', '--convert-to', 'pdf', '--outdir', str(destination),
                        str(output / ('browser-timetable.' + fmt))], check=True, timeout=60)
        rendered = PdfReader(destination / 'browser-timetable.pdf')
        text = ' '.join(' '.join(p.extract_text() for p in rendered.pages).split())
        for value in ['Английский язык', 'Математика', 'Иванова А.А.', 'Петрова Б.Б.', '18,25', 'Тестовая школа', 'Класс 47']:
            assert value in text, (fmt, value)
        assert len(rendered.pages) == len(original.pages), (fmt, len(rendered.pages), len(original.pages))
        print(f'{fmt}: {len(rendered.pages)} pages match the PDF; checked text and rooms preserved.')


if __name__ == '__main__':
    main()
