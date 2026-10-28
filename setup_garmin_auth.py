#!/usr/bin/env python3
"""
Garmin Authentication Setup Script

Logs in to Garmin Connect once (with MFA if your account uses it) and prints
the token string to save as the GARMIN_TOKENS GitHub secret.

Usage:
    uv run python setup_garmin_auth.py
"""

import logging
import shutil

from garmin.utils import GarminSettings, login_with_credentials

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    """Log in to Garmin and print the GARMIN_TOKENS secret value."""
    print("=" * 70)
    print("Garmin Connect Authentication Setup")
    print("=" * 70)
    print()

    settings = GarminSettings()

    # Always start from a clean login so the printed tokens are fresh
    if settings.garmin_tokens_path.exists():
        shutil.rmtree(settings.garmin_tokens_path)

    garmin = login_with_credentials(settings)
    token_string = garmin.client.dumps()

    print()
    print("=" * 70)
    print("Authentication successful!")
    print("=" * 70)
    print()
    print("Save the line below as the GitHub secret GARMIN_TOKENS")
    print("(repo -> Settings -> Secrets and variables -> Actions):")
    print()
    print("-" * 70)
    print(token_string)
    print("-" * 70)
    print()
    print("Keep it private: it gives access to your Garmin account.")
    print(f"A copy is also saved locally in {settings.garmin_tokens_path}/")


if __name__ == "__main__":
    main()
