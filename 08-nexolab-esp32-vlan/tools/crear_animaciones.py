"""Crea GIF de los MP4 grabados para mostrarlos dentro del README, sin visor externo."""
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ancho', type=int, default=720)
    parser.add_argument('--fps', type=int, default=5)
    args = parser.parse_args()
    output = ROOT / 'evidencias' / 'animaciones'
    output.mkdir(parents=True, exist_ok=True)
    videos = sorted((ROOT / 'evidencias' / 'videos').glob('*.mp4'))
    if not videos:
        raise SystemExit('No hay videos: ejecuta primero tools/ejecutar_evidencias.py.')
    for video in videos:
        target = output / (video.stem + '.gif')
        filters = (f'fps={args.fps},scale={args.ancho}:-1:flags=lanczos,split[a][b];'
                   '[a]palettegen=max_colors=96:stats_mode=diff[p];'
                   '[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle')
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(video),
                        '-filter_complex', filters, '-loop', '0', str(target)], check=True)
        print(f'{target.relative_to(ROOT)} ({target.stat().st_size / 1048576:.2f} MiB)', flush=True)


if __name__ == '__main__':
    main()
