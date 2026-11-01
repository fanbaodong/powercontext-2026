import argparse
import json
import sys
from pathlib import Path

from .config import load_env
from .workflow import export, prepare, resume


def main():
    parser = argparse.ArgumentParser(description='MiniMax SDD context workflow')
    parser.add_argument('command', choices=['prepare', 'resume', 'export'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--apply', action='store_true', dest='apply_candidate')
    parser.add_argument('--replay-record', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    load_env()
    if sum(bool(x) for x in (args.generate, args.apply_candidate, args.replay_record)) > 1:
        parser.error('Choose generation, reviewed apply, or recorded replay')
    if args.command == 'prepare':
        result = prepare(args.run.resolve())
    elif args.command == 'resume':
        result = resume(args.run.resolve(), generate=args.generate, apply_candidate=args.apply_candidate, replay=args.replay_record)
    else:
        if args.output is None:
            parser.error('export requires --output')
        result = export(args.run.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
