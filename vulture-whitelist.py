"""Names vulture reports as unused although they are used.

Klipper calls the first group by convention; the U1's own objects call the second. The file is
never imported: each line only uses the name once, so vulture counts it.
"""

# Klipper
load_config

# The U1's wheel and port objects call these
get_filament_detected
get_last_report_time
get_rpm
