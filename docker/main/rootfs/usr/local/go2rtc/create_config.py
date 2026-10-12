"""Creates the go2rtc config file in the container image.

The generation itself lives in ``frigate.util.go2rtc_config`` so that a native
install can reuse exactly the same logic. This script only exists because the
s6 go2rtc service stages the config before Frigate starts.
"""

import sys

sys.path.insert(0, "/opt/frigate")

from frigate.util.go2rtc_config import main  # noqa: E402

if __name__ == "__main__":
    main()
