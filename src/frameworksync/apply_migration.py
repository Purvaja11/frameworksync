import argparse
from pathlib import Path

from frameworksync.migration_transformer import apply_migration


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply an approved FrameworkSync migration."
    )

    parser.add_argument(
        "--file",
        required=True,
        help="Python file to modify.",
    )

    parser.add_argument(
        "--signal",
        required=True,
        help="Migration signal name.",
    )

    parser.add_argument(
        "--line",
        required=True,
        type=int,
        help="1-based source line to modify.",
    )

    args = parser.parse_args()

    path = Path(args.file).resolve()

    if not path.exists():
        print(f"ERROR: file does not exist: {path}")
        raise SystemExit(1)

    if not path.is_file():
        print(f"ERROR: not a file: {path}")
        raise SystemExit(1)

    changed = apply_migration(
        path=path,
        signal_name=args.signal,
        line=args.line,
    )

    if not changed:
        print("Migration was not applied.")
        raise SystemExit(1)

    print(
        f"Migration applied successfully: "
        f"{path}:{args.line}"
    )


if __name__ == "__main__":
    main()