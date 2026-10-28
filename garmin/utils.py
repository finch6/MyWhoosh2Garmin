import logging
import sys
from datetime import datetime, timedelta
from getpass import getpass
from pathlib import Path
from typing import List, Optional, Tuple

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).parent.parent
# Written only when Garmin hands out new tokens during a run; the workflow
# then saves it back to the GARMIN_TOKENS secret.
UPDATED_TOKENS_FILE = PROJECT_DIR / "data" / "garmin_tokens_updated.json"


class GarminSettings(BaseSettings):
    """Configuration settings for Garmin API client."""

    garmin_username: str | None = Field(
        default=None,
        alias="GARMIN_USERNAME",
        description="Garmin Connect username, only used for a first-time login.",
    )
    garmin_password: SecretStr | None = Field(
        default=None,
        alias="GARMIN_PASSWORD",
        description="Garmin Connect password, only used for a first-time login.",
    )
    garmin_tokens_path: Path = Field(
        default=PROJECT_DIR / ".garminconnect",
        description="Local folder where garminconnect keeps its tokens.",
    )
    garmin_tokens: SecretStr | None = Field(
        default=None,
        alias="GARMIN_TOKENS",
        description="Token JSON from garminconnect (Garmin().client.dumps()).",
    )

    model_config = SettingsConfigDict(env_file=PROJECT_DIR / ".env", extra="ignore")


_garmin: Optional[Garmin] = None
_initial_tokens: Optional[str] = None


def get_garmin() -> Garmin:
    """Return the logged-in Garmin client (call authenticate_to_garmin first)."""
    if _garmin is None:
        raise RuntimeError("Not logged in to Garmin - call authenticate_to_garmin().")
    return _garmin


def login_with_credentials(garmin_settings: GarminSettings) -> Garmin:
    """Interactive first-time login (local use). Saves tokens to the token folder."""
    username = garmin_settings.garmin_username or input("Garmin email: ").strip()
    password = (
        garmin_settings.garmin_password.get_secret_value()
        if garmin_settings.garmin_password
        else getpass("Garmin password: ")
    )
    garmin = Garmin(
        username, password, prompt_mfa=lambda: input("Enter MFA code: ").strip()
    )
    garmin.login(str(garmin_settings.garmin_tokens_path))
    return garmin


def authenticate_to_garmin(garmin_settings: GarminSettings = GarminSettings()):
    """
    Log in to Garmin Connect.

    Order: GARMIN_TOKENS (GitHub Actions) -> saved local token folder ->
    interactive username/password login (local only).

    Exits with status 1 if authentication fails.
    """
    global _garmin, _initial_tokens
    try:
        if garmin_settings.garmin_tokens:
            tokens = garmin_settings.garmin_tokens.get_secret_value()
            garmin = Garmin()
            garmin.login(tokens)
            _initial_tokens = tokens
            logger.info("Authenticated to Garmin using GARMIN_TOKENS.")
        elif (garmin_settings.garmin_tokens_path / "garmin_tokens.json").exists():
            garmin = Garmin()
            garmin.login(str(garmin_settings.garmin_tokens_path))
            logger.info("Authenticated to Garmin using saved tokens.")
        elif sys.stdin.isatty():
            logger.info("No Garmin tokens found. Logging in with credentials...")
            garmin = login_with_credentials(garmin_settings)
            logger.info("Authenticated to Garmin with credentials.")
        else:
            logger.error(
                "No GARMIN_TOKENS available. Run setup_garmin_auth.py locally "
                "and save its output as the GARMIN_TOKENS secret."
            )
            sys.exit(1)
    except GarminConnectTooManyRequestsError as e:
        logger.error(f"Garmin is rate-limiting logins, try again later: {e}")
        sys.exit(1)
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as e:
        logger.error(
            f"Garmin authentication failed: {e}. If GARMIN_TOKENS has expired, "
            "run setup_garmin_auth.py again and update the secret."
        )
        sys.exit(1)

    _garmin = garmin
    save_tokens_if_changed()  # login may already have refreshed them


def save_tokens_if_changed() -> bool:
    """
    If Garmin refreshed our tokens during this run, write the new ones to
    UPDATED_TOKENS_FILE so the workflow can update the GARMIN_TOKENS secret.
    Returns True if a file was written.
    """
    if _garmin is None or _initial_tokens is None:
        return False
    current = _garmin.client.dumps()
    if current == _initial_tokens:
        return False
    UPDATED_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    UPDATED_TOKENS_FILE.write_text(current)
    UPDATED_TOKENS_FILE.chmod(0o600)
    logger.info("Garmin tokens were refreshed; saved for the workflow to update.")
    return True


def upload_fit_file_to_garmin(new_file_path: Path):
    """Upload a .fit file to Garmin Connect."""
    if not new_file_path or not new_file_path.exists():
        logger.info(f"Invalid file path: {new_file_path}.")
        return
    try:
        result = get_garmin().upload_activity(str(new_file_path))
        logger.debug(result)
        logger.info(f"Uploaded {new_file_path.name} to Garmin Connect.")
    except GarminConnectConnectionError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status == 409 or "409" in str(e) or "duplicate" in str(e).lower():
            logger.info("Duplicate activity found on Garmin Connect.")
        else:
            raise


def list_virtual_cycling_activities(
    last_n_days: int = 30,
) -> Tuple[List[str], List[datetime]]:
    """Return two lists: activity names and start times of virtual cycling activities from Garmin Connect."""  # noqa: E501
    logger.info(
        f"Retrieving virtual cycling activities from the last {last_n_days} days..."
    )
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=last_n_days)
    activities = get_garmin().get_activities_by_date(
        start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
    )
    names, start_times = [], []
    for activity in activities:
        if activity.get("activityType", {}).get("typeKey") == "virtual_ride":
            names.append(activity["activityName"])
            start_time_str = activity.get("startTimeLocal", "")
            try:
                start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                start_time = None
            start_times.append(start_time)
            logger.debug(
                f"Found virtual cycling activity: {activity['activityName']} at {activity.get('startTimeLocal', '')} with elapsed time {activity.get('elapsedTime', '')}."  # noqa: E501
            )
    return names, start_times


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    authenticate_to_garmin()
    names, start_times = list_virtual_cycling_activities()
    for name, start_time in zip(names, start_times):
        print(f"Activity: {name}, Start Time: {start_time}")
