#!/usr/bin/env python3

import argparse
import os
import subprocess
from pathlib import Path


ENV_FILE = Path("/opt/ai-dev-agent/.env")


def load_env() -> None:
    if not ENV_FILE.is_file():
        raise FileNotFoundError(f"Env file not found: {ENV_FILE}")

    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("migration_file")
    return parser.parse_args()


def main() -> None:
    load_env()

    args = parse_args()
    migration_file = Path(args.migration_file)

    if not migration_file.is_file():
        raise FileNotFoundError(f"Migration file not found: {migration_file}")

    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ["POSTGRES_PASSWORD"]

    subprocess.run(
        [
            "psql",
            "-v", "ON_ERROR_STOP=1",
            "-h", os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "-p", os.getenv("POSTGRES_PORT", "15432"),
            "-U", os.environ["POSTGRES_USER"],
            "-d", os.environ["POSTGRES_DB"],
            "-f", str(migration_file),
        ],
        env=env,
        check=True,
    )

    print(f"Migration applied: {migration_file}")


if __name__ == "__main__":
    main()
