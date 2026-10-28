import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import garth
from dotenv import load_dotenv
from fit_tool.fit_file import FitFile
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.session_message import SessionMessage

from fit_utils.fit_builder import MyWhooshFitBuilder
from garmin.utils import (
    authenticate_to_garmin,
    list_virtual_cycling_activities,
    upload_fit_file_to_garmin,
)
from strava.client import StravaClientBuilder
from strava.utils import sanitize_filename

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=SCRIPT_DIR / ".env")

log_file_path = SCRIPT_DIR / "myWhoosh2Garmin.log"
RAW_FIT_FILE_PATH = SCRIPT_DIR / "data" / "raw"
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(log_file_path)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# --- Naming / enrichment settings (.env) ------------------------------------
try:
    ATHLETE_FTP = int(os.getenv("ATHLETE_FTP", "0").strip() or 0)
except ValueError:
    ATHLETE_FTP = 0
APPEND_MYWHOOSHINFO_URL = os.getenv("APPEND_MYWHOOSHINFO_URL", "true").strip().lower() in (
    "true", "1", "yes", "y", "on",
)

# Names that aren't a real workout title, so get no MyWhooshInfo link
GENERIC_NAMES = {
    "", "mywhoosh", "virtual ride", "virtual cycling", "indoor cycling",
    "cycling", "ride", "workout", "activity", "untitled",
}


# --- Naming helpers (ported from the other version) --------------------------
def clean_workout_name(name: str) -> str:
    """Strips a leading 'MyWhoosh' tag (e.g. 'MyWhoosh - Spiked Aerobic #1')."""
    cleaned = re.sub(r"^\s*mywhoosh\s*[-:|–]*\s*", "", name or "", flags=re.IGNORECASE)
    return cleaned.strip() or (name or "").strip()


def generate_workout_slug(workout_name: str) -> str:
    """'Spiked Aerobic #1' -> 'spiked-aerobic-1'. Empty for generic names."""
    cleaned = clean_workout_name(workout_name)
    if cleaned.lower() in GENERIC_NAMES:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", cleaned.lower()).strip("-")


def generate_fit_filename(start: datetime, workout_name: str) -> str:
    """YYYYMMDD_HHMMSS_<slug>.fit, e.g. 20261028_074156_spiked_aerobic_1.fit"""
    slug = generate_workout_slug(workout_name).replace("-", "_") or "cycling"
    return f"{start.strftime('%Y%m%d_%H%M%S')}_{slug}.fit"


# --- Coggan metrics from the built FIT ---------------------------------------
def calculate_normalized_power(power: List[int]) -> int:
    if not power:
        return 0
    if len(power) < 30:
        return round(sum(power) / len(power))
    rolling, window = [], sum(power[:30])
    rolling.append(window / 30.0)
    for i in range(30, len(power)):
        window += power[i] - power[i - 30]
        rolling.append(window / 30.0)
    return round((sum(p ** 4 for p in rolling) / len(rolling)) ** 0.25)


def compute_metrics(fit_path: Path) -> dict:
    fit = FitFile.from_file(str(fit_path))
    power: List[int] = []
    duration = 0
    for record in fit.records:
        msg = record.message
        if isinstance(msg, RecordMessage) and getattr(msg, "power", None) is not None:
            power.append(msg.power)
        elif isinstance(msg, SessionMessage):
            duration = int(getattr(msg, "total_timer_time", 0) or 0)
    duration = duration or len(power)

    np_val = calculate_normalized_power(power)
    avg_p = sum(power) / len(power) if power else 0
    if_val = round(np_val / ATHLETE_FTP, 3) if ATHLETE_FTP > 0 else 0.0
    tss = (
        round((duration * np_val * if_val) / (ATHLETE_FTP * 3600.0) * 100.0, 1)
        if ATHLETE_FTP > 0 else 0.0
    )
    work_kj = round(avg_p * duration / 1000.0)
    return {"np": np_val, "if": if_val, "tss": tss, "work_kj": work_kj}


# --- Garmin title / description -----------------------------------------------
def find_garmin_activity_id(start: datetime, retries: int = 4) -> Optional[int]:
    """Finds the just-uploaded activity by start time (within 120 s)."""
    for attempt in range(1, retries + 1):
        time.sleep(2 * attempt)  # give Garmin time to ingest
        try:
            activities = garth.client.connectapi(
                "/activitylist-service/activities/search/activities",
                params={"limit": 5},
            )
        except Exception as e:
            logger.debug(f"Activity search failed (attempt {attempt}): {e}")
            continue
        for act in activities or []:
            raw = (act.get("startTimeLocal") or "").replace("T", " ").split(".")[0]
            try:
                act_start = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if abs((act_start - start).total_seconds()) <= 120:
                return int(act["activityId"])
    logger.warning(f"No Garmin activity found starting near {start}; title not updated.")
    return None


def update_garmin_activity(activity_id: int, workout_name: str, metrics: dict) -> None:
    title = (workout_name or "").strip()  # exact Strava name, e.g. "MyWhoosh - Spiked Aerobic #1"
    slug = generate_workout_slug(workout_name)  # cleaned only for the MyWhooshInfo URL

    lines = [f"Workout: {title}" if slug else f"Activity: {title}"]
    if slug and APPEND_MYWHOOSHINFO_URL:
        lines.append(f"Details: https://mywhooshinfo.com/workouts/workout/{slug}")
    lines.append(
        f"Metrics: NP: {metrics['np']}W | IF: {metrics['if']} | "
        f"TSS: {metrics['tss']} | Work: {metrics['work_kj']}kJ"
    )
    description = "\n".join(lines)

    try:
        garth.client.connectapi(
            f"/activity-service/activity/{activity_id}",
            method="PUT",
            json={"activityId": activity_id, "activityName": title, "description": description},
        )
        logger.info(f"Renamed Garmin activity {activity_id} to '{title}'")
    except Exception as e:
        logger.warning(f"Failed to update Garmin activity {activity_id}: {e}")


def strip_timezone(dt):
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def main():
    authenticate_to_garmin()
    client_builder = StravaClientBuilder()
    client = client_builder.with_auth().with_cookies().build()

    strava_retrieved_activities = client.get_filtered_activities()
    names, start_times = list_virtual_cycling_activities(last_n_days=7)

    start_times_no_tz = {strip_timezone(dt) for dt in start_times}

    new_activities = [
        activity
        for activity in strava_retrieved_activities
        if strip_timezone(activity.start_date_local) not in start_times_no_tz
    ]
    logger.info(
        f"Found {len(new_activities)} new virtual cycling activities to upload to Garmin."  # noqa: E501
    )

    for activity in new_activities:
        client.downloader.download_activity(activity.id)
        safe_name = sanitize_filename(activity.name)
        start = strip_timezone(activity.start_date_local)

        input_path = RAW_FIT_FILE_PATH / f"{safe_name}.json"
        output_path = (
            RAW_FIT_FILE_PATH.parent / "processed" / generate_fit_filename(start, activity.name)
        )
        builder = MyWhooshFitBuilder(input_path)
        builder.build(output_path)

        try:
            metrics = compute_metrics(output_path)
        except Exception as e:
            logger.warning(f"Could not compute metrics for {output_path.name}: {e}")
            metrics = {"np": 0, "if": 0.0, "tss": 0.0, "work_kj": 0}

        upload_fit_file_to_garmin(output_path)

        activity_id = find_garmin_activity_id(start)
        if activity_id:
            update_garmin_activity(activity_id, activity.name, metrics)

        try:
            output_path.unlink()
            logger.info(f"Deleted file: {output_path}")
        except Exception as e:
            logger.error(f"Failed to delete file {output_path}: {e}")


if __name__ == "__main__":
    main()
