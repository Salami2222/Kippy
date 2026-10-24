"""Local Kippy launcher; compatible with the installed 'web' command."""
import argparse
from pathlib import Path
from .web import serve


def main():
    parser = argparse.ArgumentParser(description='Kippy — your local academic study assistant')
    parser.add_argument('command', nargs='?', choices=['web'], default='web')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--data-dir', type=Path, help='Persistent Kippy storage (default: KIPPY_DATA_DIR or ~/.kippy)')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    return serve(args.port, args.data_dir, open_browser=not args.no_browser)


if __name__ == '__main__':
    raise SystemExit(main())
