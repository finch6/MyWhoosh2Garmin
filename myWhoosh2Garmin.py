import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from fit_utils.fit_builder import MyWhooshFitBuilder
from garmin.utils import (
    authenticate_to_garmin,
    get_garmin,
    list_virtual_cycling_activities,
    save_tokens_if_changed,
    upload_fit_file_to_garmin,
)
from strava.client import StravaClientBuilder
from strava.utils import sanitize_filename

SCRIPT_DIR = Path(__file__).resolve().parent
log_file_path = SCRIPT_DIR / "myWhoosh2Garmin.log"
RAW_FIT_FILE_PATH = SCRIPT_DIR / "data" / "raw"
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler(log_file_path)
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


def get_ftp(client):
    """FTP from your Strava profile; falls back to the ATHLETE_FTP variable."""
    try:
        response = client.auth.session.get("https://www.strava.com/api/v3/athlete")
        response.raise_for_status()
        ftp = response.json().get("ftp")
        if ftp:
            logger.info(f"Using FTP from Strava profile: {ftp}W")
            return int(ftp)
        logger.info("No FTP returned by Strava (not set, or token lacks profile:read_all).")
    except Exception as e:
        logger.warning(f"Could not read FTP from Strava: {e}")

    try:
        ftp = int(os.getenv("ATHLETE_FTP", "").strip())
        logger.info(f"Using FTP from ATHLETE_FTP variable: {ftp}W")
        return ftp
    except ValueError:
        logger.info("No FTP available; IF and TSS will be left out.")
        return None


def build_description(json_path, name, ftp):
    """Notes for the Garmin activity, using the numbers Strava already calculated."""
    try:
        meta = json.loads(json_path.read_text())["metadata"]
    except Exception as e:
        logger.warning(f"Could not read Strava data from {json_path.name}: {e}")
        return None

    np_val = round(meta.get("weighted_average_watts") or 0)
    work_kj = round(meta.get("kilojoules") or 0)
    duration = meta.get("moving_time") or meta.get("elapsed_time") or 0

    metrics = []
    if np_val:
        metrics.append(f"NP: {np_val}W")
        if ftp:
            if_val = round(np_val / ftp, 2)
            tss = round(duration * np_val * if_val / (ftp * 3600) * 100, 1)
            metrics += [f"IF: {if_val}", f"TSS: {tss}"]
    if work_kj:
        metrics.append(f"Work: {work_kj}kJ")
    return ("Metrics: " + " | ".join(metrics)) if metrics else None


def rename_garmin_activity(start, name, description=None):
    """Find the just-uploaded Garmin activity by start time and give it the Strava name."""
    for attempt in range(1, 5):
        time.sleep(2 * attempt)  # give Garmin a moment to process the upload
        try:
            activities = get_garmin().connectapi(
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
                activity_id = act["activityId"]
                fields = {"activityName": name}
                if description:
                    fields["description"] = description
                try:
                    get_garmin().update_activity(activity_id, fields)
                    logger.info(f"Renamed Garmin activity {activity_id} to '{name}'")
                except Exception as e:
                    logger.warning(f"Failed to rename Garmin activity {activity_id}: {e}")
                return
    logger.warning(f"No Garmin activity found starting near {start}; name not changed.")


def main():
    authenticate_to_garmin()
    client_builder = StravaClientBuilder()
    client = client_builder.with_auth().with_cookies().build()

    strava_retrieved_activities = client.get_filtered_activities()
    names, start_times = list_virtual_cycling_activities(last_n_days=7)

    def strip_timezone(dt):
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    start_times_no_tz = {strip_timezone(dt) for dt in start_times}

    new_activities = [
        activity
        for activity in strava_retrieved_activities
        if strip_timezone(activity.start_date_local) not in start_times_no_tz
    ]
    logger.info(
        f"Found {len(new_activities)} new virtual cycling activities to upload to Garmin."  # noqa: E501
    )
    ftp = get_ftp(client) if new_activities else None

    for activity in new_activities:
        client.downloader.download_activity(activity.id)
        safe_name = sanitize_filename(activity.name)
        file_name = f"{safe_name}.json"
        input_path = RAW_FIT_FILE_PATH / file_name
        output_path = RAW_FIT_FILE_PATH.parent / "processed" / f"{safe_name}.fit"
        builder = MyWhooshFitBuilder(input_path)
        builder.build(output_path)
        upload_fit_file_to_garmin(output_path)
        description = build_description(input_path, activity.name, ftp)
        rename_garmin_activity(
            strip_timezone(activity.start_date_local), activity.name, description
        )
        try:
            output_path.unlink()
            logger.info(f"Deleted file: {output_path}")
        except Exception as e:
            logger.error(f"Failed to delete file {output_path}: {e}")

    save_tokens_if_changed()


if __name__ == "__main__":
    main()
