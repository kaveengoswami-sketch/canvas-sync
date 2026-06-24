"""Convert local daily schedule times into GitHub Actions UTC cron expressions."""


def local_times_to_cron(times, offset_hours):
    """Convert a list of local 24-hour 'HH:MM' time strings into GitHub
    Actions UTC cron expressions for a DAILY schedule.

    times: list of strings like ["09:00", "17:00", "00:00"]
    offset_hours: int, the local timezone's UTC offset (e.g. -7 for US
                  Pacific Daylight). May be negative or fractional-safe.

    Returns: list of 5-field cron strings "M H * * *" in UTC, one per input
    time, preserving input order, skipping blanks/invalid entries.

    Conversion: total_local_minutes = HH*60 + MM
                utc_minutes = (total_local_minutes - round(offset_hours*60)) % 1440
                hour = utc_minutes // 60 ; minute = utc_minutes % 60
                cron = f"{minute} {hour} * * *"
    """
    result = []
    if not times:
        return result

    offset_minutes = round(offset_hours * 60)

    for entry in times:
        if not isinstance(entry, str):
            continue
        value = entry.strip()
        parts = value.split(":")
        if len(parts) != 2:
            continue
        hh_str, mm_str = parts[0], parts[1]
        # Require exactly 2 digits each (rejects "9:5", "", etc.).
        if len(hh_str) != 2 or len(mm_str) != 2:
            continue
        if not (hh_str.isdigit() and mm_str.isdigit()):
            continue
        hh = int(hh_str)
        mm = int(mm_str)
        if not (0 <= hh <= 23):
            continue
        if not (0 <= mm <= 59):
            continue

        total_local_minutes = hh * 60 + mm
        utc_minutes = (total_local_minutes - offset_minutes) % 1440
        hour = utc_minutes // 60
        minute = utc_minutes % 60
        result.append(f"{minute} {hour} * * *")

    return result


if __name__ == "__main__":
    assert local_times_to_cron(["09:00", "17:00", "00:00"], -7) == [
        "0 16 * * *",
        "0 0 * * *",
        "0 7 * * *",
    ]
    assert local_times_to_cron(["09:00"], -8) == ["0 17 * * *"]
    assert local_times_to_cron(["23:30"], 0) == ["30 23 * * *"]
    assert local_times_to_cron(["00:00"], 1) == ["0 23 * * *"]
    assert local_times_to_cron(["bad", "", "9:5", "09:05"], -7) == ["5 16 * * *"]
    print("schedule.py self-test OK")
